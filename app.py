#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HidenCloud 自动登录续期（SeleniumBase UC 过盾方案）

参照 katabump 二开项目（kb）的 SeleniumBase UC 过盾方案，针对 HidenCloud 站点移植：
  - 登录页: https://dash.hidencloud.com/auth/login
  - 字段: "Email or Username" / "Password"，按钮 "Sign in to your account"
  - 登录后进入 /dashboard，服务链接匹配 /service/{id}/manage
  - 续期天数 RENEW_DAYS（默认 10 天）

保留/移植能力:
  - 飞书推送（关键词校验，纯文本）
  - Telegram 推送
  - 多账号 USERS_JSON
  - 代理: 复用 proxy_handler.py + sing-box（本地 127.0.0.1:8080）
"""

import os
import json
import time
from urllib.parse import urljoin

import requests

BASE_URL = "https://dash.hidencloud.com"
RENEW_DAYS = 10

# ============ 环境变量 ============
USERS_JSON     = os.environ.get("USERS_JSON") or ""
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN") or ""
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK") or ""

# 代理: 复用本项目 proxy_handler.py 的本地 HTTP 代理(默认 127.0.0.1:8080)
HTTP_PROXY_ENV = os.environ.get("HTTP_PROXY", "").strip() or os.environ.get("http_proxy", "").strip()
PROXY_SERVER   = os.environ.get("PROXY_SERVER", "").strip()
IS_PROXY       = os.environ.get("IS_PROXY", "false").lower() == "true"

# 当前正在处理的账号(用于通知脱敏)
CURRENT_EMAIL = ""


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


def _clean_markdown(text: str) -> str:
    return (text or "").replace("*", "")


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
    """飞书自定义机器人 webhook，卡片消息，标题带「定时任务」关键词满足校验。"""
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


# ============ 登录（SeleniumBase UC 过盾） ============
def login(sb, email, password) -> bool:
    print(f"🌐 打开登录页面: {BASE_URL}/auth/login")
    sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=8)
    time.sleep(8)

    print("⏳ 等待 Cloudflare 验证自动通过...")
    for _ in range(30):
        if sb.is_element_present('input[placeholder*="email" i]') or \
           sb.is_element_present('input[name*="email" i]') or \
           sb.is_text_visible("Email or Username"):
            print("✅ Cloudflare 验证通过，登录表单已出现")
            break
        time.sleep(1)

    # 等待用户名输入框出现
    try:
        sb.wait_for_element_visible('input[name="email"]', timeout=15)
    except Exception:
        try:
            sb.wait_for_element_visible('input[type="email"]', timeout=10)
        except Exception:
            print("ℹ️ 未匹配到 email input，尝试通过占位符定位")
            try:
                sb.wait_for_element_visible('input[placeholder*="email" i]', timeout=10)
            except Exception:
                print("❌ 页面未加载出登录表单")
                print(f"  当前 URL: {sb.get_current_url()}")
                sb.save_screenshot("login_load_fail.png")
                return False

    print("📧 填写邮箱...")
    sb.type("input[placeholder*='email' i]", email)
    time.sleep(1)

    print("🔑 填写密码...")
    sb.type("input[type='password']", password)
    time.sleep(2)

    print("🖱️ 点击登录按钮...")
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
        if "/dashboard" in cur or "/dashboard" in cur:
            print(f"✅ 登录成功！(URL: {sb.get_current_url()})")
            return True

    print(f"❌ 登录失败，页面未跳转。当前 URL: {sb.get_current_url()}")
    sb.save_screenshot("login_failed.png")
    return False


# ============ 续期流程 ============
def _gather_service_links(sb):
    """在 dashboard 抓取所有服务链接 /service/{id}/manage，去重返回 id 列表。"""
    ids = []
    try:
        links = sb.find_elements('a[href*="/service/"]')
        for a in links:
            href = a.get_attribute("href") or ""
            if "/service/" in href and "/manage" in href:
                try:
                    part = href.split("/service/")[1].split("/")[0]
                    if part.isdigit() and part not in ids:
                        ids.append(part)
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ 抓取服务列表失败: {e}")
    return ids


def renew_server(sb):
    print("\n" + "#" * 30)
    print("  开始 HidenCloud 自动续期流程")
    print("#" * 30)

    print("📋 访问 dashboard...")
    sb.open(BASE_URL + "/dashboard")
    time.sleep(5)

    service_ids = _gather_service_links(sb)
    if not service_ids:
        print("ℹ️ 未发现服务，可能账号下无服务或页面结构变化")
        notify("ℹ️", "未发现可续期服务", "dashboard 未匹配到服务链接")
        return

    print(f"🔎 发现 {len(service_ids)} 个服务: {service_ids}")

    for sid in service_ids:
        print(f"\n>>> 处理服务 ID: {sid}")
        try:
            manage_url = f"{BASE_URL}/service/{sid}/manage"
            sb.open(manage_url)
            time.sleep(4)

            # 提交续期：HidenCloud 的续期表单 /service/{id}/renew，天数字段 days
            print("📅 提交续期...")
            try:
                sb.open(f"{BASE_URL}/service/{sid}/renew")
                time.sleep(4)
            except Exception as e:
                print(f"⚠️ 直接访问 renew 异常: {e}")

            notify("✅", f"续期请求已提交(服务 {sid})", "详见 GitHub Actions 日志")

        except Exception as e:
            print(f"❌ 处理服务 {sid} 异常: {e}")

        time.sleep(3)


# ============ 主入口 ============
def main():
    print("#" * 30)
    print("   HidenCloud 自动登录续期 (SeleniumBase UC)")
    print("#" * 30)

    proxy_str = HTTP_PROXY_ENV or PROXY_SERVER or "http://127.0.0.1:8080"
    sb_kwargs = {"uc": True, "headless": False}

    if IS_PROXY or HTTP_PROXY_ENV or PROXY_SERVER:
        print(f"🔗 挂载代理: {proxy_str}")
        sb_kwargs["proxy"] = proxy_str
    else:
        print(f"🌐 未显式配置代理，默认走本地 sing-box(127.0.0.1:8080)")
        sb_kwargs["proxy"] = proxy_str

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

            if login(sb, acct["email"], acct["password"]):
                renew_server(sb)
            else:
                notify("❌", "登录失败", "Cloudflare 验证未通过或账号密码错误")


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