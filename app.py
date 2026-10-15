#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HidenCloud 自动登录续期（SeleniumBase UC 过盾 + 纯 HTTP 续期）

架构：
  1. 登录/过盾：SeleniumBase UC 模式（可过 Cloudflare Turnstile），登录成功后
     从浏览器取出 Cookie 与 CSRF token。
  2. 续期/支付：用 requests 纯 HTTP 忠实复刻原 local_renew.js 的核心逻辑
     （processService / checkAndPayInvoices / performPayFromHtml）。

依赖: seleniumbase, requests
"""

import os
import json
import re
import socket
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://dash.hidencloud.com"
RENEW_DAYS = 10

# ============ 环境变量 ============
USERS_JSON     = os.environ.get("USERS_JSON") or ""
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN") or ""
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK") or ""

# 代理（仅登录阶段 SeleniumBase 使用；续期 requests 直接走直连）
HTTP_PROXY_ENV = os.environ.get("HTTP_PROXY", "").strip() or os.environ.get("http_proxy", "").strip()
PROXY_SERVER   = os.environ.get("PROXY_SERVER", "").strip()
PROXY_URL      = os.environ.get("PROXY_URL", "").strip()

# 有 PROXY_URL 时 workflow 会拉起本地 sing-box，浏览器走它的 HTTP inbound
LOCAL_SINGBOX = "http://127.0.0.1:8080"

CURRENT_EMAIL = ""


def describe_login_page(html: str) -> dict:
    """Return non-sensitive markers used to distinguish login and CF pages."""
    source = (html or "").lower()
    has_username = bool(re.search(r'<input[^>]+name=[\"\']username[\"\']', source))
    has_password = bool(re.search(r'<input[^>]+type=[\"\']password[\"\']', source))
    has_challenge = (
        "challenges.cloudflare.com" in source
        or "cf-chl-" in source
        or "just a moment" in source
    )
    return {
        "login_form": has_username and has_password,
        "cloudflare_challenge": has_challenge,
        "turnstile_present": "cf-turnstile-response" in source,
    }


def requests_proxy_mapping(proxy: str | None) -> dict:
    """Create requests' proxy configuration from SeleniumBase's proxy value."""
    return {"http": proxy, "https": proxy} if proxy else {}


def should_attempt_uc_challenge(state: dict, attempts: int) -> bool:
    """Return whether an unresolved Cloudflare challenge may be retried."""
    return bool(state.get("cloudflare_challenge")) and attempts < 3


def extract_cookie_values(cookies) -> dict:
    """Normalize Selenium cookie objects and dictionary cookies for requests."""
    values = {}
    for cookie in cookies or []:
        if isinstance(cookie, dict):
            name = cookie.get("name")
            value = cookie.get("value")
        else:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
        if name is not None and value is not None:
            values[str(name)] = str(value)
    return values


# ============ 账户加载 ============
def load_accounts():
    accounts = []
    if USERS_JSON:
        try:
            data = json.loads(USERS_JSON)
            for item in data:
                if not isinstance(item, dict):
                    continue
                acct = {
                    "email": str(item.get("username") or item.get("email") or "").strip(),
                    "password": str(item.get("password") or "").strip(),
                }
                if acct["email"] and acct["password"]:
                    accounts.append(acct)
            print(f"✅ 从 USERS_JSON 加载 {len(accounts)} 个账号")
        except Exception as e:
            print(f"⚠️ USERS_JSON 解析失败: {e}")
    return accounts


# ============ 通知推送 ============
def _mask_email(email: str) -> str:
    if "@" in email:
        name, domain = email.split("@", 1)
        if len(name) > 4:
            return f"{name[:2]}****{name[-2:]}@{domain}"
        return f"{name}@{domain}"
    return (email[:2] + "****") if len(email) > 4 else email


def _beijing_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))


def send_telegram(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 Telegram, 跳过 TG 推送")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[Telegram] 发送失败: {r.text}")
    except Exception as e:
        print(f"[Telegram] 发送异常: {e}")


def send_feishu(text: str):
    if not FEISHU_WEBHOOK:
        print("ℹ️ 未配置飞书 webhook, 跳过飞书推送")
        return
    try:
        r = requests.post(FEISHU_WEBHOOK, json={
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "定时任务 · HidenCloud 续期报告"},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": text}}
                ],
            },
        }, timeout=30)
        data = r.json()
        if data.get("StatusCode") == 0 or data.get("code") == 0:
            print("[Feishu] 消息已发送")
        else:
            print(f"[Feishu] 发送失败: {data}")
    except Exception as e:
        print(f"[Feishu] 发送异常: {e}")


def notify(status_icon: str, status_text: str, detail: str = ""):
    text = (
        f"**HidenCloud 续期报告**\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 账号: {_mask_email(CURRENT_EMAIL)}\n"
        f"⏱️ 时间: {_beijing_now()}"
    )
    if detail:
        text += f"\n📄 详情: {detail}"
    send_telegram(text)
    send_feishu(text)


# ============ 纯 HTTP 续期核心（复刻 local_renew.js） ============
class HidenCloudClient:
    """用 requests 复刻 local_renew.js 的 axios 续期逻辑。"""

    COMMON_HEADERS = {
        "Host": "dash.hidencloud.com",
        "Connection": "keep-alive",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Referer": BASE_URL + "/",
    }

    def __init__(self, cookies: dict, csrf: str, proxy: str | None = None):
        self.cookies = cookies
        self.csrf = csrf
        self.session = requests.Session()
        self.session.headers.update(self.COMMON_HEADERS)
        self.session.cookies.update(cookies)
        self.session.proxies.update(requests_proxy_mapping(proxy))

    def _full(self, url: str) -> str:
        """绝对 URL 直接返回；相对路径拼到 BASE_URL。"""
        if url.startswith("http"):
            return url
        return urljoin(BASE_URL, url)

    def get(self, url):
        return self.session.get(self._full(url), timeout=30)

    def post(self, url, data, headers=None, allow_redirects=True):
        h = {"X-CSRF-TOKEN": self.csrf}
        if headers:
            h.update(headers)
        return self.session.post(self._full(url), data=data, headers=h, timeout=30,
                                 allow_redirects=allow_redirects)


def _extract_meta_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", {"name": "csrf-token"})
    return meta.get("content", "") if meta else ""


def _extract_form_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.find("input", {"name": "_token"})
    return inp.get("value", "") if inp else ""


def _extract_service_ids(html: str):
    """复刻 $('a[href*="/service/"]') 匹配 /service/{id}/manage 去重。"""
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r'/service/(\d+)/manage', href)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def initialize_http_session_from_dashboard(response):
    """Validate the requests session and derive fresh dashboard state."""
    final_url = (getattr(response, "url", "") or "").lower()
    if "/auth/login" in final_url or "/login" in final_url:
        return None
    html = getattr(response, "text", "") or ""
    return {
        "csrf": _extract_meta_csrf(html),
        "service_ids": _extract_service_ids(html),
    }


def _extract_invoice_links(html: str):
    """复刻 checkAndPayInvoices: 找 /invoice/ 链接，排除 download。"""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/invoice/" in href and "download" not in href and href not in links:
            links.append(href)
    return links


def _find_pay_form(html: str):
    """复刻 performPayFromHtml: 找按钮文本含 pay 的 form，返回 (action, inputs)。

    inputs: [(name, value), ...]。action 不能含 balance/add。
    """
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form", action=True):
        action = form.get("action", "")
        if not action or "balance/add" in action:
            continue
        # 按钮文本（含 submit button 或 input[type=submit]）
        btn_text = ""
        for btn in form.find_all(["button", "input"]):
            if btn.name == "input" and btn.get("type", "").lower() != "submit":
                continue
            t = (btn.get("value") or btn.get_text() or "").strip().lower()
            if t:
                btn_text = t
                break
        if "pay" not in btn_text:
            continue
        inputs = []
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                inputs.append((name, inp.get("value", "") or ""))
        return action, inputs
    return None, []


def process_service(client, sid):
    """复刻 local_renew.js 的 processService。"""
    print(f">>> 处理服务 ID: {sid}")
    try:
        resp = client.get(f"/service/{sid}/manage")
        form_token = _extract_form_token(resp.text)
        if not form_token:
            print(
                "❌ 服务管理页未找到 _token，取消续期："
                f"status={resp.status_code}, url={resp.url}"
            )
            return

        print(f"📅 提交续期 ({RENEW_DAYS} 天)...")
        data = {"_token": form_token, "days": str(RENEW_DAYS)}
        # 原 JS 用 maxRedirects:0，靠响应头 Location 判断跳转；这里 allow_redirects=False 对齐
        resp = client.post(
            f"/service/{sid}/renew",
            data,
            {"Referer": f"{BASE_URL}/service/{sid}/manage"},
            allow_redirects=False,
        )

        location = resp.headers.get("Location", "")
        final_url = location or resp.url
        if "/invoice/" in final_url:
            print("⚡️ 续期成功，前往支付")
            # 原 JS 对 302 会递归 request('GET', location) 拿 invoice 页 HTML，
            # 再解析支付表单。这里显式 GET 一遍再解析。
            try:
                inv_resp = client.get(final_url)
                perform_pay_from_html(client, inv_resp.text, inv_resp.url)
            except Exception as e:
                print(f"⚠️ 打开 invoice 页失败: {e}")
        else:
            print("⚠️ 续期后未跳转，检查账单列表...")
            check_and_pay_invoices(client, sid)
    except Exception as e:
        print(f"❌ 处理异常: {e}")


def check_and_pay_invoices(client, sid):
    """复刻 local_renew.js 的 checkAndPayInvoices。"""
    try:
        resp = client.get(f"/service/{sid}/invoices?where=unpaid")
        invoices = _extract_invoice_links(resp.text)
        if not invoices:
            print("✅ 无未支付账单")
            return
        for url in invoices:
            pay_single_invoice(client, url)
            time.sleep(3)
    except Exception as e:
        print(f"❌ 查账单出错: {e}")


def pay_single_invoice(client, url):
    """复刻 local_renew.js 的 paySingleInvoice。"""
    try:
        print(f"📄 打开账单: {url}")
        resp = client.get(url)
        perform_pay_from_html(client, resp.text, url)
    except Exception as e:
        print(f"❌ 访问失败: {e}")


def perform_pay_from_html(client, html, current_url):
    """复刻 local_renew.js 的 performPayFromHtml。"""
    action, inputs = _find_pay_form(html)
    if action is None:
        print("⚪ 页面未找到支付表单 (可能已支付)")
        return

    payload = {}
    for name, value in inputs:
        if name:
            payload[name] = value

    print("💳 提交支付...")
    try:
        resp = client.post(action, payload, {"Referer": current_url})
        if resp.status_code == 200:
            print("✅ 支付成功！")
        else:
            print(f"⚠️ 支付响应: {resp.status_code}")
    except Exception as e:
        print(f"❌ 支付失败: {e}")


def renew_all(client, service_ids):
    print(f"🔎 发现 {len(service_ids)} 个服务: {service_ids}")
    for sid in service_ids:
        process_service(client, sid)
        time.sleep(3)


# ============ 登录（SeleniumBase UC 过盾） ============
def _save_login_diagnostics(sb, stage: str, state: dict) -> None:
    """Save failure evidence without exposing credentials, cookies, or tokens."""
    safe_email = re.sub(r"[^a-zA-Z0-9_.-]+", "_", CURRENT_EMAIL.split("@", 1)[0])[:32] or "account"
    filename = f"login_{stage}_{safe_email}.png"
    try:
        print(
            "ℹ️ 登录页诊断: "
            f"url={sb.get_current_url()}, title={sb.get_title() or ''}, "
            f"login_form={state['login_form']}, "
            f"cloudflare_challenge={state['cloudflare_challenge']}, "
            f"turnstile_present={state['turnstile_present']}"
        )
        sb.save_screenshot(filename)
        print(f"📸 已保存登录失败截图: {filename}")
    except Exception as exc:
        print(f"⚠️ 保存登录诊断失败: {exc}")


def _try_uc_challenge(sb, attempt: int) -> None:
    """Give SeleniumBase UC one bounded opportunity to interact with CF."""
    print(f"🖱️ 检测到 Cloudflare 挑战，尝试 UC 验证 ({attempt}/3)...")
    try:
        sb.uc_gui_click_captcha()
    except Exception as exc:
        print(f"⚠️ UC 验证操作未完成: {exc}")


def login_and_get_session(sb, email, password):
    """UC 过盾登录，成功后返回 (cookies_dict, csrf_token) 或 (None, None)。"""
    print(f"🌐 打开登录页面: {BASE_URL}/auth/login")
    sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=8)
    time.sleep(8)

    print("⏳ 等待 Cloudflare 验证与登录表单...")
    last_state = describe_login_page("")
    challenge_attempts = 0
    for second in range(60):
        try:
            last_state = describe_login_page(sb.get_page_source())
        except Exception:
            last_state = describe_login_page("")
        if last_state["login_form"]:
            print("✅ Cloudflare 验证通过，登录表单已出现")
            break
        if (should_attempt_uc_challenge(last_state, challenge_attempts)
                and second in (5, 20, 35)):
            challenge_attempts += 1
            _try_uc_challenge(sb, challenge_attempts)
        time.sleep(1)
    else:
        print(f"❌ 60s 内未出现登录表单，当前 URL: {sb.get_current_url()}")
        _save_login_diagnostics(sb, "no_form", last_state)
        return None, None

    # 定位输入框：HidenCloud 用 name="username"（placeholder "Enter your email or username"）
    email_sel = ""
    for sel in ("#username", "input[name='username']",
                "input[placeholder*='email' i]", "input[type='email']"):
        if sb.is_element_present(sel):
            email_sel = sel
            break
    if not email_sel:
        print("❌ 未找到用户名输入框")
        _save_login_diagnostics(sb, "no_username", last_state)
        return None, None

    print("📧 填写邮箱...")
    sb.type(email_sel, email)
    time.sleep(1)

    print("🔑 填写密码...")
    sb.type("input[type='password']", password)
    time.sleep(2)

    # 表单里的 cf-turnstile-response 必须由 Turnstile 自动填充，否则提交必然被拒
    print("⏳ 等待 Turnstile token 生成...")
    token_ready = False
    turnstile_attempts = 0
    for second in range(30):
        val = sb.execute_script(
            "return (document.querySelector('input[name=\"cf-turnstile-response\"]') || {}).value || '';"
        )
        if val:
            print(f"✅ Turnstile token 已生成 ({len(val)} 字符)")
            token_ready = True
            break
        try:
            turnstile_state = describe_login_page(sb.get_page_source())
        except Exception:
            turnstile_state = describe_login_page("")
        if (should_attempt_uc_challenge(turnstile_state, turnstile_attempts)
                and second in (3, 12, 21)):
            turnstile_attempts += 1
            _try_uc_challenge(sb, turnstile_attempts)
        time.sleep(1)
    if not token_ready:
        if turnstile_state["turnstile_present"]:
            print("❌ Turnstile token 未生成，取消提交以避免必然失败")
            _save_login_diagnostics(sb, "turnstile_no_token", turnstile_state)
            return None, None
        print("ℹ️ 页面未检测到 Turnstile，继续提交登录表单")

    print("🖱️ 点击登录...")
    try:
        sb.click("button[type='submit']")
    except Exception:
        try:
            sb.click("button:contains('Sign in')")
        except Exception:
            sb.press_keys("input[type='password']", "\n")

    print("⏳ 等待登录跳转...")
    for _ in range(20):
        time.sleep(1)
        cur = sb.get_current_url().lower()
        if "/dashboard" in cur:
            print(f"✅ 登录成功！(URL: {sb.get_current_url()})")
            # 取 cookie 与 csrf
            cookies = extract_cookie_values(sb.get_all_cookies())
            html = sb.get_page_source()
            csrf = _extract_meta_csrf(html)
            if not csrf:
                csrf = sb.get_attribute("meta[name='csrf-token']", "content") or ""
            return cookies, csrf

    print(f"❌ 登录失败，页面未跳转。当前 URL: {sb.get_current_url()}")
    _save_login_diagnostics(sb, "submit_failed", describe_login_page(sb.get_page_source()))
    return None, None


# ============ 主入口 ============
def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """等待本地端口可连（sing-box 是 nohup 后台起的，就绪时间不确定）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    print("#" * 30)
    print("   HidenCloud 自动登录续期 (UC 过盾 + HTTP 续期)")
    print("#" * 30)

    # 优先级：显式 HTTP_PROXY/PROXY_SERVER > PROXY_URL(workflow 已拉起本地 sing-box) > 直连
    proxy_str = HTTP_PROXY_ENV or PROXY_SERVER
    if not proxy_str and PROXY_URL:
        if _wait_port("127.0.0.1", 8080):
            proxy_str = LOCAL_SINGBOX
        else:
            print("⚠️ 已配置 PROXY_URL 但本地 sing-box (127.0.0.1:8080) 未就绪，回退直连")

    sb_kwargs = {"uc": True, "headless": False}
    if proxy_str:
        print(f"🔗 挂载代理: {proxy_str}")
        sb_kwargs["proxy"] = proxy_str
    else:
        print("🌐 未配置代理，浏览器走直连")

    accounts = load_accounts()
    if not accounts:
        print("❌ 未配置任何账号: 请设置 USERS_JSON")
        return

    print(f"📋 共 {len(accounts)} 个账号待续期")
    for idx, acct in enumerate(accounts, 1):
        global CURRENT_EMAIL
        CURRENT_EMAIL = acct["email"]
        print("\n" + "=" * 30)
        print(f"📧 账号 {idx}/{len(accounts)}: {acct['email']}")
        print("=" * 30)

        try:
            from seleniumbase import SB
        except ImportError:
            print("❌ seleniumbase 未安装, 请先 pip install seleniumbase")
            continue

        with SB(**sb_kwargs) as sb:
            try:
                sb.open("https://api.ip.sb/ip")
                print(f"📍 当前出口IP: {sb.get_text('body')}")
            except Exception:
                pass

            try:
                cookies, csrf = login_and_get_session(sb, acct["email"], acct["password"])
            except Exception as e:
                print(f"❌ 登录过程异常: {e}")
                sb.save_screenshot("login_error.png")
                cookies, csrf = None, None
            if cookies is None:
                notify("❌", "登录失败", "Cloudflare 验证未通过或账号密码错误")
                continue

            # 浏览器登录成功后，必须用同一代理验证 HTTP 会话并刷新页面 CSRF。
            client = HidenCloudClient(cookies, csrf, proxy_str)
            try:
                dash = client.get("/dashboard")
                http_state = initialize_http_session_from_dashboard(dash)
            except Exception as e:
                print(f"❌ HTTP dashboard 会话验证失败: {e}")
                notify("❌", "HTTP 会话验证失败", str(e))
                continue

            if http_state is None:
                print(f"❌ HTTP 会话未保持登录: status={dash.status_code}, url={dash.url}")
                notify("❌", "HTTP 会话未保持登录", f"status={dash.status_code}")
                continue

            csrf = http_state["csrf"] or csrf
            client.csrf = csrf
            service_ids = http_state["service_ids"]
            print(
                f"✅ HTTP 会话验证成功: status={dash.status_code}, "
                f"发现 {len(service_ids)} 个服务"
            )

            if not service_ids:
                print("ℹ️ 未发现服务")
                notify("ℹ️", "未发现可续期服务", "dashboard 未匹配到服务链接")
                continue

            renew_all(client, service_ids)
            notify("✅", "续期流程已执行", f"共 {len(service_ids)} 个服务")


def _random_delay():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event != "schedule":
        print("[Anti-Detection] 手动触发: 跳过随机延迟")
        return
    import random
    delay_sec = random.randint(0, 5 * 60)
    m, s = divmod(delay_sec, 60)
    print(f"[Anti-Detection] 定时触发: 随机延迟 {m}m {s}s...")
    time.sleep(delay_sec)


if __name__ == "__main__":
    _random_delay()
    main()
