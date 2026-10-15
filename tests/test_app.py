import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app


class LoginPageStateTests(unittest.TestCase):
    def test_describe_login_page_recognizes_hidencloud_username_form(self):
        state = app.describe_login_page(
            '<input name="username"><input type="password">'
        )

        self.assertTrue(state["login_form"])
        self.assertFalse(state["cloudflare_challenge"])

    def test_describe_login_page_detects_cloudflare_turnstile(self):
        state = app.describe_login_page(
            '<iframe src="https://challenges.cloudflare.com/turnstile"></iframe>'
            '<input name="cf-turnstile-response">'
        )

        self.assertFalse(state["login_form"])
        self.assertTrue(state["cloudflare_challenge"])
        self.assertTrue(state["turnstile_present"])

    def test_turnstile_challenge_remains_actionable_when_login_form_is_visible(self):
        state = app.describe_login_page(
            '<input name="username"><input type="password">'
            '<iframe src="https://challenges.cloudflare.com/turnstile"></iframe>'
            '<input name="cf-turnstile-response">'
        )

        self.assertTrue(app.should_attempt_uc_challenge(state, 0))
        self.assertFalse(app.should_attempt_uc_challenge(state, 3))


class ProxyConfigurationTests(unittest.TestCase):
    def test_proxy_mapping_uses_browser_proxy_for_http_requests(self):
        self.assertEqual(
            app.requests_proxy_mapping("http://127.0.0.1:8080"),
            {
                "http": "http://127.0.0.1:8080",
                "https": "http://127.0.0.1:8080",
            },
        )

    def test_proxy_mapping_is_empty_without_a_proxy(self):
        self.assertEqual(app.requests_proxy_mapping(None), {})


class CookieExtractionTests(unittest.TestCase):
    def test_extract_cookie_values_accepts_selenium_cookie_objects(self):
        cookies = [
            SimpleNamespace(name="hidencloud_session", value="session-value"),
            SimpleNamespace(name="XSRF-TOKEN", value="csrf-value"),
        ]

        self.assertEqual(
            app.extract_cookie_values(cookies),
            {
                "hidencloud_session": "session-value",
                "XSRF-TOKEN": "csrf-value",
            },
        )

    def test_extract_cookie_values_accepts_dictionary_cookies(self):
        self.assertEqual(
            app.extract_cookie_values([{"name": "session", "value": "value"}]),
            {"session": "value"},
        )


class HttpSessionInitializationTests(unittest.TestCase):
    def test_403_is_never_an_authenticated_empty_dashboard(self):
        response = SimpleNamespace(status_code=403, url=app.BASE_URL + '/dashboard',
                                   text='<title>Just a moment...</title>')
        self.assertIsNone(app.initialize_http_session_from_dashboard(response))

    def test_200_challenge_is_not_an_authenticated_dashboard(self):
        response = SimpleNamespace(status_code=200, url=app.BASE_URL + '/dashboard',
                                   text='<title>Security Verification - HidenCloud</title>')
        self.assertIsNone(app.initialize_http_session_from_dashboard(response))

    def test_dashboard_response_is_rejected_when_redirected_to_login(self):
        response = SimpleNamespace(
            url="https://dash.hidencloud.com/auth/login",
            text="<html></html>",
        )

        self.assertIsNone(app.initialize_http_session_from_dashboard(response))

    def test_dashboard_response_refreshes_csrf_and_service_ids(self):
        response = SimpleNamespace(
            url="https://dash.hidencloud.com/dashboard",
            text=(
                '<meta name="csrf-token" content="fresh-csrf">'
                '<a href="/service/101/manage">Manage</a>'
            ),
        )

        self.assertEqual(
            app.initialize_http_session_from_dashboard(response),
            {"csrf": "fresh-csrf", "service_ids": ["101"]},
        )


class BrowserEntryPointTests(unittest.TestCase):
    @patch('app.notify')
    @patch('app.HidenCloudClient')
    @patch('app.renew_service', return_value={'status': 'skipped', 'detail': 'Not yet'})
    def test_hidden_service_link_is_used_without_requests(self, renew, http, notify):
        sb = Mock()
        sb.get_current_url.return_value = app.BASE_URL + '/dashboard'
        sb.get_page_source.return_value = (
            '<title>Dashboard - HidenCloud</title><tr class="hidden">'
            '<td><a href="/service/101/manage">Manage</a></td></tr>'
        )
        self.assertTrue(app.renew_browser_services(sb))
        renew.assert_called_once_with(sb, '101')
        http.assert_not_called()
        self.assertEqual(notify.call_args.args[0], '⏳')

    @patch('app.notify')
    @patch('app.renew_service', side_effect=RuntimeError('Browser unavailable'))
    def test_service_failure_is_not_reported_as_success(self, renew, notify):
        sb = Mock()
        sb.get_current_url.return_value = app.BASE_URL + '/dashboard'
        sb.get_page_source.return_value = '<a href="/service/101/manage">Manage</a>'
        self.assertFalse(app.renew_browser_services(sb))
        self.assertEqual(notify.call_args.args[0], '❌')

    @patch('app.notify')
    @patch('app.renew_service')
    def test_challenge_page_stops_service_discovery(self, renew, notify):
        sb = Mock()
        sb.get_current_url.return_value = app.BASE_URL + '/dashboard'
        sb.get_page_source.return_value = '<title>Security Verification - HidenCloud</title>'
        self.assertFalse(app.renew_browser_services(sb))
        renew.assert_not_called()


if __name__ == "__main__":
    unittest.main()
