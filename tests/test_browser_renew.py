import unittest
from unittest.mock import Mock, patch

import browser_renew


def renewal_html(restricted=False, captcha=True):
    trigger = ('onclick="showRenewAlert(3, 1, true)"' if restricted
               else 'data-modal-target="renewService-101"')
    field = '<input name="cf-turnstile-response">' if captcha else ''
    return (
        '<button type="button" ' + trigger + '>Renew</button>'
        '<form id="renew-form-101" action="/service/101/renew" method="post">'
        '<input name="_token" value="test-token">'
        '<select name="days"><option value="7">1 Week - €0.00</option></select>'
        + field + '<button type="submit">Create Invoice</button></form>'
    )


class RenewalInspectionTests(unittest.TestCase):
    def test_restriction_is_read_from_real_button_without_submitting(self):
        state = browser_renew.inspect_renewal_form(renewal_html(True), '101')
        self.assertEqual(state['status'], 'skipped')
        self.assertIn('3', state['detail'])
        self.assertIn('1', state['detail'])

    def test_reads_seven_day_option_and_form_scoped_captcha(self):
        state = browser_renew.inspect_renewal_form(renewal_html(), '101')
        self.assertEqual(state['days'], '7')
        self.assertEqual(state['selector'], '#renew-form-101')
        self.assertTrue(state['captcha'])
        self.assertEqual(state['opener'], 'button[data-modal-target="renewService-101"]')

    def test_missing_csrf_form_or_period_is_a_failure(self):
        for html in ('', renewal_html().replace('name="_token"', 'name="other"'),
                     renewal_html().replace('value="7"', 'value=""')):
            with self.subTest(html=html):
                self.assertEqual(browser_renew.inspect_renewal_form(html, '101')['status'], 'failed')

    def test_rejects_invalid_service_id(self):
        with self.assertRaises(ValueError):
            browser_renew.inspect_renewal_form('', '101" onclick="bad')

    def test_paid_detection_ignores_unpaid_and_balance_modal(self):
        self.assertTrue(browser_renew.is_paid_invoice('<main>Invoice #123 <span>Paid</span></main>'))
        self.assertFalse(browser_renew.is_paid_invoice('<main>Invoice #123 Unpaid</main>'))
        self.assertFalse(browser_renew.is_paid_invoice('<main>Invoice #123 Unpaid</main><div>Paid</div>'))


class BrowserRenewalFlowTests(unittest.TestCase):
    def test_login_redirect_never_attempts_renewal(self):
        sb = Mock()
        sb.get_current_url.return_value = browser_renew.BASE_URL + '/auth/login'
        self.assertEqual(browser_renew.renew_service(sb, '101')['status'], 'failed')
        sb.click.assert_not_called()

    @patch('browser_renew.time.sleep')
    def test_no_captcha_form_can_submit(self, sleep):
        sb = Mock()
        sb.get_page_source.side_effect = [renewal_html(captcha=False), '<main>Invoice #123 Paid</main>']
        sb.get_current_url.side_effect = [browser_renew.BASE_URL + '/service/101/manage',
                                         browser_renew.BASE_URL + '/payment/invoice/123']
        # No exact Paid node means the page cannot count as payment confirmation.
        self.assertEqual(browser_renew.renew_service(sb, '101')['status'], 'failed')
        sb.execute_script.assert_not_called()

    @patch('browser_renew.time.sleep')
    def test_failed_post_is_not_repeated(self, sleep):
        sb = Mock()
        sb.get_page_source.side_effect = [renewal_html(captcha=False),
                                         '<div role="alert">Captcha validation failed</div>']
        sb.get_current_url.return_value = browser_renew.BASE_URL + '/service/101/manage'
        result = browser_renew.renew_service(sb, '101')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Captcha', result['detail'])
        self.assertEqual(sb.click.call_count, 2)  # opener, then one submit

    def test_restricted_service_never_opens_or_submits_form(self):
        sb = Mock()
        sb.get_current_url.return_value = browser_renew.BASE_URL + '/service/101/manage'
        sb.get_page_source.return_value = renewal_html(True)
        result = browser_renew.renew_service(sb, '101')
        self.assertEqual(result['status'], 'skipped')
        sb.click.assert_not_called()

    @patch('browser_renew.time.sleep')
    def test_empty_renewal_captcha_never_submits_invoice(self, sleep):
        sb = Mock()
        sb.get_current_url.return_value = browser_renew.BASE_URL + '/service/101/manage'
        sb.get_page_source.return_value = renewal_html()
        sb.execute_script.return_value = ''
        result = browser_renew.renew_service(sb, '101')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(sb.click.call_count, 1)  # modal opener only

    @patch('browser_renew.time.sleep')
    def test_invoice_paid_after_native_renewal_submission(self, sleep):
        sb = Mock()
        sb.get_page_source.side_effect = [renewal_html(), '<main>Invoice #123 <span>Paid</span></main>']
        sb.get_current_url.side_effect = [browser_renew.BASE_URL + '/service/101/manage',
                                         browser_renew.BASE_URL + '/payment/invoice/123']
        sb.execute_script.return_value = 'test-captcha'
        result = browser_renew.renew_service(sb, '101')
        self.assertEqual(result['status'], 'paid')
        sb.select_option_by_value.assert_called_once_with('#renew-form-101 select[name="days"]', '7')


class InvoiceCompletionTests(unittest.TestCase):
    def test_ambiguous_submit_actions_are_not_clicked(self):
        sb = Mock()
        sb.get_page_source.return_value = '<main>Invoice #123 <span>Paid</span></main>'
        html = ('<main>Invoice #123 <form action="/payment/invoice/123/pay">'
                '<button type="submit">Cancel</button><button type="submit">Pay</button>'
                '</form></main>')
        self.assertEqual(browser_renew._finish_invoice(sb, html)['status'], 'failed')
        sb.click.assert_not_called()

    @patch('browser_renew.time.sleep')
    def test_payment_waits_for_paid_marker(self, sleep):
        sb = Mock()
        sb.get_page_source.return_value = '<main>Invoice #123 <span>Paid</span></main>'
        html = ('<main>Invoice #123 <span>Unpaid</span>'
                '<form action="/payment/invoice/123/pay"><input name="_token" value="test">'
                '<button type="submit">Pay</button></form></main>')
        self.assertEqual(browser_renew._finish_invoice(sb, html)['status'], 'paid')
        sb.click.assert_called_once()

    @patch('browser_renew.time.sleep')
    def test_payment_200_without_paid_marker_is_not_success(self, sleep):
        sb = Mock()
        sb.get_page_source.return_value = '<main>Invoice #123 <span>Unpaid</span></main>'
        html = ('<main>Invoice #123 <form action="/payment/invoice/123/pay">'
                '<button type="submit">Pay</button></form></main>')
        self.assertEqual(browser_renew._finish_invoice(sb, html)['status'], 'failed')
        sb.click.assert_called_once()

    def test_balance_or_offsite_form_is_not_used_for_invoice_payment(self):
        sb = Mock()
        for action in ('/balance/add', 'https://other.example/pay'):
            with self.subTest(action=action):
                html = '<main>Invoice #123 <form action="' + action + '"><button>Pay</button></form></main>'
                self.assertEqual(browser_renew._finish_invoice(sb, html)['status'], 'failed')
        sb.click.assert_not_called()

    @patch('browser_renew.time.sleep')
    def test_missing_payment_captcha_does_not_pay(self, sleep):
        sb = Mock()
        sb.execute_script.return_value = ''
        html = ('<main>Invoice #123 <form action="/payment/invoice/123/pay">'
                '<input name="cf-turnstile-response"><button>Pay</button></form></main>')
        self.assertEqual(browser_renew._finish_invoice(sb, html)['status'], 'failed')
        sb.click.assert_not_called()


class SubmissionObservationTests(unittest.TestCase):
    def test_redirect_outside_site_or_back_to_auth_fails(self):
        for url in (browser_renew.BASE_URL + '/auth/login', 'https://other.example/invoice/123'):
            sb = Mock()
            sb.get_current_url.return_value = url
            self.assertEqual(browser_renew._wait_invoice(sb)['status'], 'failed')

    @patch('browser_renew.time.sleep')
    def test_timeout_does_not_resubmit(self, sleep):
        sb = Mock()
        sb.get_current_url.return_value = browser_renew.BASE_URL + '/service/101/manage'
        sb.get_page_source.return_value = '<div>Still waiting</div>'
        self.assertEqual(browser_renew._wait_invoice(sb)['status'], 'failed')
        sb.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
