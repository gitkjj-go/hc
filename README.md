# HidenCloud 自动续期脚本

[🇬🇧 English](./README_EN.md) | 🇨🇳 简体中文

## 📖 简介

这是一个用于 HidenCloud 服务自动续期的脚本，支持本地运行、GitHub Actions 云端运行和青龙面板三种部署方式。可以自动完成续期和支付操作，并智能管理 Cookie 缓存。

当前 `renew1.yml` 工作流运行 Python 的 `app.py`：UC 登录后继续在同一个浏览器中续期，避免复制 Cookie 到 requests 后被返回 403。续期规则和诊断见 [浏览器续期说明](docs/hidencloud-browser-renewal.md)。下文的 Node.js/Cookie 缓存说明适用于保留的旧脚本。

## ✨ 主要特性

- ☁️ **多种部署方式**：本地运行 / GitHub Actions / 青龙面板
- 🔄 **Cookie 自动持久化**：自动更新并缓存最新 Cookie
- 👥 **多账号支持**：支持同时处理多个账号（最多 10 个）
- 💳 **自动支付**：自动完成续期后的支付流程
- 📊 **详细日志**：实时输出处理进度和结果
- 🛡️ **智能重试**：Cookie 失效时自动回退重试
- 🔐 **安全可靠**：GitHub Actions 自动更新仓库变量中的 Cookie
- 📜 **工作流对比**：提供多种[工作流选择](./WORKFLOWS.md)，灵活应对验证策略

## 🚀 部署方式

### 方式一：本地运行（推荐新手）

**前置要求：**

- Node.js (建议 v14 或更高版本)
- npm 包依赖：`axios`, `cheerio`, `playwright`, `playwright-extra`, `puppeteer-extra-plugin-stealth`

**快速开始：**

详见 [快速开始指南](./start.md)

### 方式二：GitHub Actions（推荐）

完全云端自动化，无需本地环境，自动更新 Cookie。

**配置步骤：**

1. **Fork 本仓库**到你的 GitHub 账号
2. **Fork 本仓库**到你的 GitHub 账号
3. **设置仓库 Secret**

   - 进入你 Fork 的仓库 → Settings → Secrets and variables → Actions
   - 点击 New repository secret
   - Name: `USERS_JSON`
   - Secret: 粘贴你的账号配置 JSON（格式如下）
     ```json
     [
       {"username": "user1@example.com", "password": "password123"},
       {"username": "user2@example.com", "password": "password456"}
     ]
     ```
4. **启用 GitHub Actions**

   - 进入 Actions 标签
   - 如果看到提示，点击 "I understand my workflows, go ahead and enable them"
5. **手动运行测试**

   - Actions → Katabump Auto Renew New → Run workflow
   - 查看运行日志确认成功

### Telegram 通知配置 (可选)

如果需要任务执行结果通知，可以配置 Telegram Bot：

1. **获取配置信息**：
   - `TG_BOT_TOKEN`: 联系 [@BotFather](https://t.me/BotFather) 创建机器人获取 Token
   - `TG_CHAT_ID`: 联系 [@userinfobot](https://t.me/userinfobot) 获取你的 ID，或邀请机器人入群获取群组 ID

2. **添加 Secrets**：
   - 同样在 Settings → Secrets and variables → Actions 中添加：
     - Name: `TG_BOT_TOKEN`
     - Secret: 你的 Bot Token
     - Name: `TG_CHAT_ID`
     - Secret: 你的 User ID 或 Group ID

配置完成后，每次任务执行结束都会发送简要报告到你的 Telegram。

**工作流说明：**

- 自动运行：每 3 天自动触发
- 手动触发：可随时在 Actions 页面手动运行
- Cookie 自动更新：执行完成后会自动更新仓库变量中的 Cookie

### 方式三：青龙面板

适合已有青龙面板的用户。

**使用方法：**

1. 复制 `qinglong.js` 到青龙面板
2. 设置环境变量 `HIDEN_COOKIE`，多账号用 `&` 或换行分隔
3. 定时规则：`0 10 */7 * *`（每 7 天运行）

详见文件内注释说明。

## 🚀 使用方法

### 1. 安装依赖

npm install

### 2. 准备 Cookie 文件

在脚本同目录下创建 `cookie.json` 文件，格式如下：

```json
{
    "cookie1": "你的第一个账号的完整Cookie字符串",
    "cookie2": "你的第二个账号的完整Cookie字符串",
    "cookie3": ""
}
```

**说明：**

- 字段名必须为 `cookie1`, `cookie2`, `cookie3` 等格式
- 留空或不填的字段会被自动忽略
- Cookie 获取方法见下文

### 3. 运行脚本

```bash
node local_renew.js
```

## 🍪 如何获取 Cookie

### 方法一：浏览器开发者工具

1. 登录 [HidenCloud Dashboard](https://dash.hidencloud.com)
2. 按 `F12` 打开开发者工具
3. 切换到 `Network` (网络) 标签
4. 刷新页面
5. 点击任意请求，查看 `Request Headers`
6. 复制 `Cookie` 字段的完整内容

### 方法二：浏览器扩展

使用 Cookie 导出扩展（如 EditThisCookie、Cookie-Editor）直接导出。

### 方法三：Windows 自动获取（推荐）

如果您是在 Windows 本地运行，可以使用提供的自动登录脚本来生成 Cookie。

1. **准备账号文件**：在项目根目录创建 `users.json`，格式如下：

   ```json
   [
     {"username": "你的邮箱", "password": "你的密码"},
     {"username": "第二个账号", "password": "密码"}
   ]
   ```
2. **运行登录脚本**：

   ```bash
   node win_login.js
   ```

   脚本会自动打开 Chrome 浏览器进行登录，通过验证后将 Cookie 保存到 `cookie.json`。
3. **配置 Chrome 路径**：
   打开 `win_login.js`，找到 `const CHROME_PATH = ...` 这一行。
   将路径修改为你本机 Chrome 的实际安装路径（例如 `'D:\\Software\\Chrome\\Application\\chrome.exe'`）。
#### **一键运行**：
   双击 `start.bat` 脚本。
   它会自动执行：**登录获取 Cookie** → **生成 `cookie.json`** → **执行续期**。

## ⚙️ 配置说明

脚本内的可配置参数（在 `local_renew.js` 顶部）：

```javascript
const RENEW_DAYS = 10;  // 续期天数，默认 10 天
const COOKIE_FILE = path.join(__dirname, 'cookie.json');  // Cookie 文件路径
const CACHE_FILE = path.join(__dirname, 'hiden_cookies_cache.json');  // 缓存文件路径
```

## 📊 运行示例

```
╔════════════════════════════════════════════╗
║   HidenCloud 本地自动续期脚本 v2.0        ║
╚════════════════════════════════════════════╝

📋 共找到 2 个账号

==================================================
开始处理: cookie1 (1/2)
==================================================
[cookie1] 🔄 发现本地缓存 Cookie，优先使用...
[cookie1] 🔍 正在验证登录状态...
[cookie1] ✅ 登录成功，发现 3 个服务
[cookie1] >>> 处理服务 ID: 12345
[cookie1] 📅 提交续期 (10天)...
[cookie1] ⚡️ 续期成功，前往支付
[cookie1] 💳 提交支付...
[cookie1] ✅ 支付成功！
💾 [cookie1] 最新 Cookie 已保存到本地缓存

╔════════════════════════════════════════════╗
║              续期结果汇总                  ║
╚════════════════════════════════════════════╝

📊 cookie1:
   ✅ 成功续期 3 个服务
📊 cookie2:
   ✅ 成功续期 2 个服务

✨ 脚本执行完毕！
```

## ⚠️ 注意事项

1. **Cookie 安全**：请妥善保管 `cookie.json` 文件，不要泄露给他人
2. **定期更新**：Cookie 可能会过期，失效时请及时更新
3. **运行频率**：建议设置定时任务，每 7 天运行一次
4. **网络环境**：确保网络能正常访问 hidencloud.com

## 🤖 定时任务设置

### Windows 任务计划程序

1. 打开「任务计划程序」
2. 创建基本任务
3. 触发器设置为每 7 天运行一次
4. 操作选择「启动程序」
5. 程序/脚本填写 `node`
6. 添加参数填写脚本完整路径

### Linux/Mac Crontab

```bash
# 编辑 crontab
crontab -e

# 添加定时任务（每周一上午10点运行）
0 10 * * 1 cd /path/to/hidencloud && node local_renew.js >> renew.log 2>&1
```

## 🆚 部署方式对比

| 特性            | 本地运行     | GitHub Actions                           | 青龙面板    |
| --------------- | ------------ | ---------------------------------------- | ----------- |
| 运行环境        | 本地 Node.js | GitHub 云端                              | 青龙容器    |
| Cookie 来源     | cookie.json  | 仓库变量                                 | 环境变量    |
| 自动定时        | 需手动设置   | ✅ 内置                                  | ✅ 内置     |
| Cookie 自动更新 | ✅ 本地缓存  | ❌ 每次IP不同，保存COOKIE下次也无法使用 | ✅ 本地缓存 |
| 消息推送        | ❌           | ❌                                       | ✅          |
| 多账号支持      | ✅           | ✅                                       | ✅          |
| 推荐指数        | ⭐⭐⭐       | ⭐⭐⭐⭐⭐                               | ⭐⭐⭐⭐    |

## 🐛 问题排查

### Cookie 失效

**现象**：提示「当前 Cookie 已失效」

**解决**：

1. 重新登录 HidenCloud
2. 获取最新 Cookie
3. 更新 `cookie.json`

### 依赖安装失败

**现象**：`npm install` 报错

**解决**：

```bash
# 清除缓存
npm cache clean --force

# 重新安装
npm install
```

### 网络超时

**现象**：请求超时或连接失败

**解决**：

1. 检查网络连接
2. 尝试使用代理
3. 增加脚本中的 `timeout` 值

## 📜 许可证

MIT License

## 🙏 致谢

感谢 [gally16](https://linux.do/u/gally16) 提供的青龙脚本！本项目在此基础上进行了优化和Github Actions部署和windows部署。
