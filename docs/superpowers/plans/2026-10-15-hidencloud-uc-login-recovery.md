# HidenCloud UC Login Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably diagnose and recover the HidenCloud Cloudflare-gated login flow before HTTP renewal.

**Architecture:** Keep HidenCloud-specific DOM recognition separate from SeleniumBase actions. The login loop will inspect a small page-state object, invoke UC captcha interaction only when a Cloudflare challenge exists, and save account-scoped failure evidence. HTTP renewal will inherit the browser proxy to keep its network route consistent.

**Tech Stack:** Python 3, SeleniumBase UC, requests, BeautifulSoup, standard-library unittest.

---

### Task 1: Login page state recognition

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

```python
def test_describe_login_page_recognizes_hidencloud_username_form():
    state = app.describe_login_page(
        '<input name=\"username\"><input type=\"password\">'
    )
    assert state["login_form"] is True
    assert state["cloudflare_challenge"] is False
```

- [ ] **Step 2: Run the test and verify it fails because `describe_login_page` does not exist.**

Run: `python -m unittest tests.test_app.LoginPageStateTests -v`
Expected: FAIL with an `AttributeError` for `describe_login_page`.

- [ ] **Step 3: Implement `describe_login_page`**

```python
def describe_login_page(html: str) -> dict:
    source = (html or "").lower()
    return {
        "login_form": bool(re.search(r'<input[^>]+(?:name=[\"\']username|type=[\"\']password)', source)),
        "cloudflare_challenge": "challenges.cloudflare.com" in source or "cf-chl-" in source,
        "turnstile_present": "cf-turnstile-response" in source,
    }
```

- [ ] **Step 4: Run the test and verify it passes.**

Run: `python -m unittest tests.test_app.LoginPageStateTests -v`
Expected: PASS.

### Task 2: Controlled UC recovery and proxy consistency

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

```python
def test_proxy_mapping_uses_browser_proxy_for_http_requests():
    assert app.requests_proxy_mapping("http://127.0.0.1:8080") == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }
```

- [ ] **Step 2: Run the test and verify it fails because `requests_proxy_mapping` does not exist.**

Run: `python -m unittest tests.test_app.ProxyConfigurationTests -v`
Expected: FAIL with an `AttributeError` for `requests_proxy_mapping`.

- [ ] **Step 3: Implement the mapping and pass it to `HidenCloudClient`. Add one bounded `uc_gui_click_captcha()` attempt per detected challenge and retain diagnostics after unsuccessful attempts.**

```python
def requests_proxy_mapping(proxy: str | None) -> dict:
    return {"http": proxy, "https": proxy} if proxy else {}
```

- [ ] **Step 4: Run focused and full test suites.**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 5: Commit only files produced by this plan.**

```bash
git add app.py tests/test_app.py docs/superpowers/plans/2026-10-15-hidencloud-uc-login-recovery.md
git commit -m "fix: recover HidenCloud UC login challenges"
```
