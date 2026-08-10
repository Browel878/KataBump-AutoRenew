# KataBump Auto Renew 🔄

KataBump 免费服务器自动续期，基于 **Python + Playwright + CDP** 绕过 Cloudflare Turnstile。

> 每 4 天需续期一次，过期 1 天即永久删除！

## ✨ 特性

- 🛡️ **CDP 过盾**: 注入脚本获取 Turnstile 坐标 + CDP 真实鼠标事件，高成功率
- 🔄 **20 次重试**: 验证码失败自动刷新页面重来
- 👥 **多账号**: JSON 格式批量配置
- 📡 **代理支持**: Hysteria2 / SOCKS5 / HTTP 代理
- 📱 **TG 通知**: 成功/失败/跳过均推送（含截图）
- ⏱️ **防休眠**: 自动提交 time.txt

## 🚀 配置

### 1. Fork 本仓库

### 2. 配置 Secrets (Settings → Secrets → Actions)

**必需：**

| Secret | 说明 | 格式 |
|--------|------|------|
| `USERS_JSON` | 账号 | `[{"username":"邮箱","password":"密码"}]` |

多账号：
```json
[{"username":"a@example.com","password":"p1"},{"username":"b@example.com","password":"p2"}]
```

**可选：**

| Secret | 说明 | 格式 |
|--------|------|------|
| `NODE_LINK` | 订阅节点链接，配置后走 sing-box 代理登录（GitHub Action IP 被拦时必配） | `vless://...` / `vmess://...` / `hysteria2://...` 等 |
| `HY2_PROXY_URL` | Hysteria2 代理 | `hysteria2://auth@host:port?sni=xxx` |
| `HTTP_PROXY` | HTTP 代理 | `http://host:port` |
| `SOCKS_PORT` | SOCKS5 端口 | `51080` (默认) |
| `TG_BOT_TOKEN` | Telegram Bot Token | |
| `TG_CHAT_ID` | Telegram Chat ID | |

### 3. 设置权限

Settings → Actions → General → ✅ **Read and write permissions**

### 4. 启用 Workflow

Actions 页面 → Enable workflow → 可手动 Run workflow 测试

## 🔧 续期流程

1. 登录 `dashboard.katabump.com`
2. 填入邮箱密码
3. **CDP 绕 Turnstile** (Hook attachShadow → 获取坐标比例 → CDP 鼠标事件)
4. 点击 See → 点击 Renew
5. 模态框内再过一次 Turnstile → 确认续期
6. 检测：成功 / 未到时间 / 验证码失败(刷新重试)

## 📁 结构

```
├── .github/workflows/kata-renew.yml
├── kata_renew.py          # 主脚本
├── time.txt               # 自动更新(防休眠)
└── README.md
```

## 💡 server_id (可选)

在 `USERS_JSON` 中可以指定 `server_id`，脚本会直接导航到服务器页面，无需在 dashboard 查找链接：

```json
[{"username":"email@example.com","password":"pass","server_id":"123456"}]
```

如果不指定 `server_id`，脚本会尝试在 Dashboard 页面查找服务器入口链接。
