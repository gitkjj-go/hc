"""Renew through the authenticated browser and the site's own forms."""

import json
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BASE_URL = 'https://dash.hidencloud.com'


def inspect_renewal_form(html, sid):
    """Inspect the actual renewal control; never bypass a restricted button."""
    if not re.fullmatch(r'\d+', str(sid)):
        raise ValueError('Invalid service ID')
    soup = BeautifulSoup(html, 'html.parser')
    for button in soup.select('button[onclick]'):
        match = re.fullmatch(
            r'\s*showRenewAlert\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*(?:true|false)\s*\)\s*;?\s*',
            button['onclick'],
        )
        if match:
            return {'status': 'skipped', 'detail': (
                f'暂未到续期窗口：页面显示剩余 {match[1]} 天，'
                f'仅允许到期前 {match[2]} 天内续期'
            )}
    selector = f'#renew-form-{sid}'
    form = soup.select_one(selector)
    if not form or urljoin(BASE_URL, form.get('action', '')) != f'{BASE_URL}/service/{sid}/renew':
        return {'status': 'failed', 'detail': '未找到本站续期表单'}
    csrf = form.select_one('input[name="_token"]')
    options = form.select('select[name="days"] option[value]')
    options = [o for o in options if o['value'].isdigit() and int(o['value']) > 0
               and not o.has_attr('disabled')]
    if not csrf or not csrf.get('value') or not options:
        return {'status': 'failed', 'detail': '续期表单缺少 CSRF 或有效周期'}
    option = next((o for o in options if o.has_attr('selected')), options[0])
    opener = f'button[data-modal-target="renewService-{sid}"]'
    if not soup.select_one(opener):
        return {'status': 'failed', 'detail': '未找到可用的续期弹窗入口'}
    return {'status': 'ready', 'selector': selector, 'opener': opener,
            'days': option['value'], 'captcha': bool(form.select_one('[name="cf-turnstile-response"]'))}


def is_paid_invoice(html):
    """Require the exact Paid marker in invoice content, not merely HTTP 200."""
    main = BeautifulSoup(html, 'html.parser').find('main')
    return bool(main and re.search(r'\bInvoice\s*#', main.get_text(' ', strip=True), re.I)
                and main.find(string=re.compile(r'^\s*Paid\s*$', re.I)))


def _wait_captcha(sb, selector):
    # A token from the login form cannot be reused for the renewal form.
    script = (
        'return (document.querySelector('
        + json.dumps(selector + ' [name="cf-turnstile-response"]')
        + ') || {}).value || "";'
    )
    for second in range(60):
        if sb.execute_script(script):
            return True
        if second in (5, 20, 35):
            print('🖱️ 等待续期表单独立 Turnstile 验证...')
            sb.uc_gui_click_captcha()
        time.sleep(1)
    return False


def _wait_invoice(sb):
    """Observe a single submission; never retry a possibly processed POST."""
    for _ in range(30):
        url = sb.get_current_url()
        if urlparse(url).netloc != urlparse(BASE_URL).netloc or '/auth/' in url:
            return {'status': 'failed', 'detail': '提交后未保持本站登录态'}
        html = sb.get_page_source()
        if '/invoice/' in urlparse(url).path:
            return {'status': 'invoice', 'html': html}
        soup = BeautifulSoup(html, 'html.parser')
        alert = soup.select_one('[role="alert"], .alert-danger')
        if alert:
            return {'status': 'failed', 'detail': alert.get_text(' ', strip=True)[:500]}
        time.sleep(1)
    return {'status': 'failed', 'detail': '提交后未确认账单跳转，未重复提交；请检查截图'}


def _finish_invoice(sb, html):
    if is_paid_invoice(html):
        return {'status': 'paid', 'detail': '账单明确显示 Paid，续期已支付'}
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.find('main')
    for form in main.find_all('form', action=True) if main else []:
        action = form['action']
        destination = urlparse(urljoin(BASE_URL, action))
        if destination.netloc != urlparse(BASE_URL).netloc or 'balance/add' in action:
            continue
        buttons = form.select('button[type="submit"], button:not([type]), input[type="submit"]')
        if not any('pay' in (b.get('value') or b.get_text()).lower() for b in buttons):
            continue
        if len(buttons) != 1:
            return {'status': 'failed', 'detail': '支付表单存在多个提交动作，请人工检查账单'}
        selector = 'main form[action=' + json.dumps(action) + ']'
        if form.select_one('[name="cf-turnstile-response"]') and not _wait_captcha(sb, selector):
            return {'status': 'failed', 'detail': '支付表单验证码未完成，账单尚未支付'}
        sb.click(selector + ' button[type="submit"], ' + selector + ' button:not([type]), '
                 + selector + ' input[type="submit"]')
        for _ in range(30):
            if is_paid_invoice(sb.get_page_source()):
                return {'status': 'paid', 'detail': '账单明确显示 Paid，续期已支付'}
            time.sleep(1)
        return {'status': 'failed', 'detail': '已提交支付但未确认 Paid，请人工检查账单'}
    return {'status': 'failed', 'detail': '已生成账单，未发现可确认的支付表单或 Paid 状态'}


def renew_service(sb, sid):
    """Use the same browser, native modal, selected period and scoped captcha."""
    sb.open(f'{BASE_URL}/service/{sid}/manage')
    current = urlparse(sb.get_current_url())
    if current.netloc != urlparse(BASE_URL).netloc or current.path != f'/service/{sid}/manage':
        return {'status': 'failed', 'detail': '未进入已登录的服务管理页'}
    state = inspect_renewal_form(sb.get_page_source(), sid)
    if state['status'] != 'ready':
        return state
    selector = state['selector']
    sb.click(state['opener'])
    sb.wait_for_element_visible(selector, timeout=15)
    sb.select_option_by_value(selector + ' select[name="days"]', state['days'])
    if state['captcha'] and not _wait_captcha(sb, selector):
        return {'status': 'failed', 'detail': '续期验证码未生成，未提交 Create Invoice'}
    print(f'📅 按页面允许周期续期：{state["days"]} 天')
    sb.click(selector + ' button[type="submit"]')
    result = _wait_invoice(sb)
    return _finish_invoice(sb, result['html']) if result['status'] == 'invoice' else result
