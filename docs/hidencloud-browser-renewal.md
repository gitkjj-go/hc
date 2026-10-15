# HidenCloud 浏览器续期与诊断

2026-10-15 实际登录后检查确认：

- dashboard 的 `/service/{id}/manage` 链接位于隐藏的服务折叠行中。读取完整 HTML 即可发现，不必展开行。
- requests 返回 403 不能被当成“登录成功，0 个服务”。原生浏览器会话可正常进入同一个服务管理页；主入口现使用该浏览器继续操作。
- 免费服务的 Renew 按钮在未到窗口时调用 `showRenewAlert(剩余天数, 门槛天数, true)`，仅显示 Renewal Restricted。程序识别此状态后跳过，不提交隐藏表单。
- 检查时页面显示剩余 3 天、门槛 1 天，续期周期下拉框唯一选项为 7 天（1 Week / €0.00），不是旧脚本固定的 10 天。
- `renew-form-{id}` 中有独立的 `cf-turnstile-response`。页面自身说明隐藏的弹窗不会运行挑战，必须正常打开弹窗并等待 token；登录表单的 token 不能替代它。
- 原生 Create Invoice 表单 POST 到 `/service/{id}/renew`。历史账单位于 `/payment/invoice/{uuid}`；仅在账单主内容明确显示 Paid 时报告已支付。

本次观察未创建账单、未提交支付。当前不在续期窗口，因此正常可续期弹窗的打开、实时验证码与创建账单后的流程还需要在窗口内通过 Actions 验证；已用模拟浏览器测试覆盖这些分支。

## 执行与结果

工作流每 6 小时检查一次（原来的每 3 天可能错过不足 1 天的窗口），也支持手动运行。脚本发现服务后逐项报告：

- `skipped`：站点限制，尚未到续期窗口；这不是故障。
- `paid`：该次流程进入的账单显示 Paid。
- `failed`：登录失效、表单/验证码缺失、未确认账单或支付结果；上传 `renew_failed_*.png`，进程以非零状态结束。

续期周期从站点实际选项读取；提交后不自动重试 POST，避免重复创建账单。主入口不再调用旧的 HTTP 续期函数。

提交/推送时必须同时包含 `app.py`、新增的 `browser_renew.py`、`tests/` 和 `.github/workflows/renew1.yml`。缺少 `browser_renew.py` 会导致启动导入失败。

本地回归命令：

```sh
python -m unittest discover -v
python -m py_compile app.py browser_renew.py
git diff --check
```

如果 Actions 显示 `Ran 0 tests`，先确认测试文件实际提交到了运行分支。仅上传 `app.py`、`browser_renew.py` 和工作流不会上传本地未跟踪的 `tests/`。工作流显式从 `tests/` 发现 `test_*.py`，因此不依赖空的 `tests/__init__.py` 是否通过网页上传；如果两份测试文件尚未上传，会显示 warning 后继续续期。测试存在但断言失败时仍会中止，不能视为通过。
