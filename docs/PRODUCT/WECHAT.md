# 微信服务号配置指南（自建部署）

> 📌 **本文档面向自建部署者。** 如果你只是想使用本服务，**无需任何配置**——直接微信扫码关注官方服务号 **「摇人吧」** 即可（见 [README](../README.md)）。
>
> 本文档介绍如何用**你自己的微信服务号**运行一套独立实例。

## 前置要求

- 一个**已认证的微信服务号**（个人订阅号无网页授权/模板消息等高级接口权限）
- 一个**公网可访问的 HTTPS 域名**（微信服务器配置与网页授权强制要求 HTTPS）

## 1. 准备参数

登录 [微信公众平台](https://mp.weixin.qq.com/) 获取：

- **AppID** 和 **AppSecret**（开发 → 基本配置）→ 填入 `.env` 的 `WECHAT_APP_ID` / `WECHAT_APP_SECRET`
- 自定义一个 **Token** 字符串 → 填入 `.env` 的 `WECHAT_TOKEN`

> ⚠️ 一个服务号的「服务器配置」URL 只能指向一个后端。若你的服务号已对接了别的系统，配置本平台会接管消息回调，请先评估影响。

## 2. 配置服务器地址（接收消息）

开发 → 基本配置 → 服务器配置：

- **URL**：`https://your.domain/api/wechat/callback`
- **Token**：与 `.env` 中 `WECHAT_TOKEN` 一致
- **EncodingAESKey**：随机生成，若填则同步到 `.env` 的 `WECHAT_AES_KEY`
- **消息加解密方式**：明文模式（简单）/ 安全模式（生产推荐）

点击「提交」时，微信会向该 URL 发起 GET 校验请求，后端 `GET /api/wechat/callback` 会校验签名并回显 `echostr`。**因此提交前，后端必须已部署且公网可访问。**

> 本地联调如何把本机暴露到公网，见 [SETUP.md 的「本地开发联调」](./SETUP.md#本地开发联调)。

## 3. 配置网页授权域名（OAuth 网页授权登录）

开发 → 接口权限 → 网页授权 → 设置授权回调域名：

- 填写你的域名（不带 `https://`），如 `your.domain`
- 按提示下载校验文件放到网站根目录（需公网 HTTPS 可访问）
- 该域名必须与前端构造授权链接时的 `redirect_uri` 域名一致，否则微信报 `10003 redirect_uri 域名与配置不一致`

前端授权流程（由 `VITE_WECHAT_LOGIN_ENABLED=true` 开启，对应 `src/config/wechat.ts`）：

1. 未登录访问 H5，前端 `AuthGuard` 直接构造并跳转微信授权页
   `https://open.weixin.qq.com/connect/oauth2/authorize?appid=APPID&redirect_uri=<后端回调>&response_type=code&scope=snsapi_base&state=<路径>&#wechat_redirect`
   - `redirect_uri` 默认 = `前端 origin + /api/wechat/callback`；但生产网关只转发 `/p/api/*`，故必须改用 `VITE_WECHAT_REDIRECT_URI=https://<域名>/p/api/wechat/callback` 覆盖（见下方「部署注意」）
   - `state` 为当前路径（把 `/` 编码为 `0`），后端回调时原样还原并跳回该页面
2. 微信回跳 `GET /api/wechat/callback?code=CODE&state=STATE`（后端路由，见 `app/wechat/api/wechat.py`）
3. 后端用 `code` + `WECHAT_APP_ID`/`WECHAT_APP_SECRET` 调微信换 `openid`，注册/登录并签发 JWT，再 `RedirectResponse` 回前端 `?token=...&refresh_token=...`
4. 前端 `checkUrlTokens()` 读取 URL 上的 token 存入 localStorage，完成登录

> **登录页 `/login` 行为**：启用微信登录（`VITE_WECHAT_LOGIN_ENABLED=true`）且访问不带 `?debug=true` 时，登录页自身也会直接跳微信授权（与受守卫页面一致）；仅当访问 `https://<域名>/p/app/login?debug=true` 时才显示账密表单，便于后台人员登录。

所需环境变量：

- 前端（`.env.[mode]`）：`VITE_WECHAT_LOGIN_ENABLED`、`VITE_WECHAT_APP_ID`、`VITE_WECHAT_OAUTH_SCOPE`、`VITE_WECHAT_REDIRECT_PATH`（可选）、`VITE_WECHAT_REDIRECT_URI`（可选）
- 后端（`.env`）：`WECHAT_APP_ID`、`WECHAT_APP_SECRET`（生产必填，见 `config.py` 的 production 校验）

### 部署注意（nginx 路由与域名校验）

本仓库的示例网关（`deploy/nginx/conf/conf.d/app_gateway.conf`）按环境前缀分流：生产前端 `/p/app/*`、生产后端 `/p/api/*`，**没有 `/api/*` 路由**（根路径兜底 404）。因此涉及微信的两条回调地址都**必须带 `/p` 前缀**：

- **网页授权 `redirect_uri`**：`https://<域名>/p/api/wechat/callback`（生产 `.env.production` 设 `VITE_WECHAT_REDIRECT_URI`，不要用默认无前缀值，否则微信回调用 404）。
- **服务器配置 URL（消息回调）**：`https://<域名>/p/api/wechat/callback`（否则微信校验签名 `echostr` 时 404，配置提交失败）。

其它上线检查项：

- 公众号「网页授权域名」校验文件（`MP_verify_*.txt`）需能通过 `https://<域名>/MP_verify_*.txt` 访问，但当前 nginx `location /` 兜底 404。需新增放行规则，例如：`location = /MP_verify_xxxx.txt { root /usr/share/nginx/html/verify; }`（把文件放到对应目录）。
- 后端 `/wechat/callback` 用 `request.url` 还原回跳地址，请确认后端信任 `X-Forwarded-Proto`（nginx 已下发该头），否则回跳协议可能变成 `http://`，在微信 HTTPS 环境被拦截。

## 4. 自定义菜单

运行脚本一键创建菜单：

```bash
cd backend
python -m app.wechat.menu_setup
```

默认菜单结构（可在 `app/wechat/menu_setup.py` 调整）：

```
┌──────────────┬──────────────┬──────────────┐
│   我要咨询    │   我的工单    │   提交工单    │
│  (H5 咨询页)  │ (H5 工单列表) │ (H5 新建工单) │
└──────────────┴──────────────┴──────────────┘
```

菜单项为 `view` 类型，跳转到前端 H5 对应页面（带 OAuth 授权）。

## 5. 模板消息

在公众平台「功能 → 模板消息 / 订阅通知」申请模板，拿到模板 ID 填入 `.env`：

- `TPL_ASSIGN_ID` —— 派单通知（标题如「您有新的工单待处理」）
- `TPL_COMMENT_ID` —— 讨论通知（「工单有新的讨论」）
- `TPL_ESCALATE_ID` —— 上报通知（「有工单上报给您」）

推荐模板字段（示例，派单通知）：

```
{{first.DATA}}
工单编号：{{keyword1.DATA}}
工单标题：{{keyword2.DATA}}
当前状态：{{keyword3.DATA}}
{{remark.DATA}}
```

字段映射在 `app/wechat/notify.py` 中，按你申请到的实际模板调整。

## 6. 没有微信环境也能开发

若 `.env` 未配置微信参数，系统进入**开发降级模式**（详见 [SETUP.md](./SETUP.md#本地开发联调)）：消息回调 / OAuth / 模板消息不会真正调用微信，通知打印到日志，可用 `POST /api/auth/dev-login` 以任意角色获取 JWT 联调全部业务流程。
