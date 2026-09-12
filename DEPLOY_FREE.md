# 免费公网部署指南（永久域名 · 电脑关机也能访问）

三条路，都免费、都给你固定域名、都不需要你电脑开着：

| 方案 | 得到什么 | 国内直连 | 界面 |
| --- | --- | --- | --- |
| A. Streamlit Community Cloud | https://xxx.streamlit.app | 通 | Streamlit |
| B. Cloudflare Pages + Render | 前端 pages.dev + 后端 onrender.com | 通 | React（原版） |
| C. 本地演示 | http://192.168.x.x:5173 | 仅同一 WiFi | React |

---

## 前置：把代码推到 GitHub（用 GitHub Desktop，不用装 Git）

1. 打开 `GitHub Desktop` -> `File` -> `Add local repository`
2. 选择本目录（AI_World）-> 若提示不是仓库，点 `create a repository` -> Create
3. 点 `Publish repository` -> **取消勾选 Keep this code private**（必须公开，Streamlit 免费版只支持公开仓库）-> Publish

> 仓库里不要放真实密钥：`.env` 已被 .gitignore 忽略，只会提交 `.env.example`。

---

## 方案 A：Streamlit Community Cloud（最快，5 分钟）

1. 打开 https://share.streamlit.io ，用 GitHub 账号登录（免费）
2. `Create app` -> `Deploy a public app from GitHub`
3. 填写：
   - Repository：你的 `用户名/AI-World`
   - Branch：`main`
   - **Main file path：`streamlit_app.py`**（若仓库根目录就是 AI_World）
   - Requirements file：`requirements.txt`（默认即可）
4. 点 `Deploy`，等 1-2 分钟
5. 得到永久域名：`https://<应用名>.streamlit.app`

### 强烈建议：加访问口令

公网链接谁拿到都能用。加口令：Streamlit Cloud -> 你的应用 -> `Settings` -> `Secrets`，粘贴：

```toml
APP_PASSWORD = "换成你自己的口令"

# 可选：站点级 DeepSeek Key。建议留空 ——
# 应用支持访客在侧边栏填自己的 Key（BYOK），留空则你零成本、零被刷风险。
DEEPSEEK_API_KEY = ""

# 可选：站长账号。写上你自己的用户名（多个用英文逗号分隔，如 "alice,bob"）；
# 用该用户名注册/登录后，左侧才会出现「📊 用量统计」，普通访客完全看不到这一项。
ADMIN_USERNAMES = "你的用户名"
```

保存后应用会自动重启，进去就需要口令。

### 站长账号（账号制，不再是口令）

站长功能不再用口令解锁，而是**认账号**：

1. Streamlit Cloud -> 你的应用 -> `Settings` -> `Secrets`，加一行
   `ADMIN_USERNAMES = "你的用户名"`（多个用英文逗号分隔，例如 `"alice,bob"`）；
2. 保存后点 `Reboot app` 让新 Secrets 生效；
3. 用这个用户名**注册或登录** —— 左侧导航才会出现「📊 用量统计」，
   页面下半部分还有「🛠 站长数据」只读区；
4. 其它普通账号登录后，左侧**完全没有**这一项；即使手动改 URL 强行进来，
   也只会看到「该页面仅站长账号可见」的提示，不会渲染任何数据。

> `APP_PASSWORD` 是"整个站点的访问口令"，和站长账号是两回事：
> 前者决定"谁能进这个站"，`ADMIN_USERNAMES` 决定"谁能看到站长页"。

---

## 方案 B：Cloudflare Pages（前端）+ Render（后端）

### B1. 部署后端到 Render

1. 打开 https://render.com -> 用 GitHub 登录（免费，无需信用卡）
2. `New` -> `Blueprint` -> 选择你的 `AI-World` 仓库 -> Apply
3. Render 会读取仓库里的 `render.yaml`，自动创建一个 Docker Web Service
4. 建好后在 `Environment` 补两个变量：
   - `DEEPSEEK_API_KEY`：你的 Key（可留空，留空即离线演示模式）
   - `CORS_ORIGINS`：先留空，等 B2 拿到前端域名后回来填
5. 记下后端地址：`https://ai-world-api.onrender.com`（名字可能不同）

> Render 免费实例闲置 15 分钟会休眠，首次打开需等 30-60 秒唤醒。
> 数据在容器内 SQLite，重新部署会重置；要持久就把 `DATABASE_URL` 指向 Neon / Supabase 的免费 Postgres。

### B2. 部署前端到 Cloudflare Pages

1. 打开 https://dash.cloudflare.com -> `Workers & Pages` -> `Create` -> `Pages` -> `Connect to Git`
2. 选择 `AI-World` 仓库
3. 构建设置：
   - Framework preset：`Vite`
   - **Root directory（根目录）：`frontend`**
   - Build command：`npm install && npm run build`
   - Build output directory：`dist`
4. 环境变量：`VITE_API_BASE_URL` = `https://ai-world-api.onrender.com/api`（换成 B1 的真实地址）
5. 保存并部署，得到 `https://ai-world.pages.dev`

### B3. 回到 Render 填 CORS

把 `CORS_ORIGINS` 改成你的 Pages 域名并保存（会自动重启）：

```
CORS_ORIGINS=https://ai-world.pages.dev
```

然后打开 Pages 域名，注册自己的账号（数据与演示账号隔离）即可使用。

---

## 接入免费云数据库（Neon）—— 让云端数据永久保留

**为什么需要**：Streamlit Cloud 免费实例的文件系统是**临时的**，重新部署/重启会清空数据库 ——
AI 伙伴、知识库、对话记录、用量统计都会没。接一个免费的云 Postgres 即可彻底解决。

### 第 1 步：注册 Neon（免费，无需信用卡）

1. 打开 https://neon.tech -> `Sign up` -> 用 GitHub 账号登录（最省事）
2. 点 `Create project`：
   - Region：选 **Singapore** 或 **US West**（离 Streamlit Cloud 近，延迟低）
   - Postgres version：默认即可
3. 创建完成后，在 `Connection string` 面板里选 **Pooled connection**（带 `-pooler` 的那种，更稳）
4. 复制整条连接串，形如：

``
postgresql://用户名:密码@ep-xxx-pooler.区域.aws.neon.tech/neondb?sslmode=require
``

### 第 2 步：贴进 Streamlit Secrets

Streamlit Cloud -> 你的应用 -> 右侧 `Settings` -> `Secrets`，加入一行：

`toml`
# 原样粘贴即可：代码会自动把 sslmode 转成 ssl、并切换成异步驱动 asyncpg
DATABASE_URL = "postgresql://用户名:密码@ep-xxx-pooler.区域.aws.neon.tech/neondb?sslmode=require"
``

保存后应用会自动重启。

### 第 3 步：确认切换成功

看侧边栏「运行状态」：

- ✅ 成功：**数据库：PostgreSQL（云端，长期保留）**
- ❌ 仍是：数据库：SQLite（本地文件）-> 说明 `DATABASE_URL` 没生效（检查拼写/引号）

之后创建的角色、上传的知识库、对话记录**都不会再因重启消失**。

### 已为你处理好的坑

| 坑 | 处理方式 |
| --- | --- |
| 平台给的是 `postgres://` 前缀 | 自动换成 `postgresql+asyncpg://` |
| 连接串里的 `?sslmode=require` | 自动换成 asyncpg 认的 `?ssl=require` |
| 附带 `channel_binding` 等 libpq 参数 | 自动丢弃 |
| 空闲挂起导致连接失效 | 已开 `pool_pre_ping` + `pool_recycle=300` |
| 首次连接需要建表 | 启动时自动 `create_all` + 只做 ADD COLUMN 的安全迁移 |

> 免费档够用：Neon Free 有 0.5GB 存储、计算实例空闲 5 分钟后挂起（下次访问自动唤醒，约 0.5 秒）。
> 本项目的云端数据量远小于这个额度。

---

## 部署 React 版（根治「切换慢」与「返回键」，并与 Streamlit 版共用同一份数据）

### 前置：两个前端共用同一个后端

React 前端只放静态页面，**所有数据都在后端**。让 React 后端连**同一个 Neon**，
这样 Streamlit 版与 React 版的账号、知识库、聊天记录是**同一份**（注册一次两边通用）。

### B1. 部署后端到 Render

1. https://render.com -> `New` -> `Blueprint` -> 选仓库 -> Apply（读取 `render.yaml`）
2. 建好后在 `Environment` 填：

```bash
DATABASE_URL     = 你 Neon 的连接串（**与 Streamlit 版填同一条**，否则数据会分裂成两份）
CORS_ORIGINS     = https://你的项目.pages.dev,https://aiworld.streamlit.app
DEEPSEEK_API_KEY = 可留空（留空则访客自带 Key）
```

3. 记下后端地址：`https://ai-world-api.onrender.com`

> Render 免费容器文件系统是临时的 —— **数据持久完全依赖 Neon**，不要用容器内 SQLite。

### B2. 部署前端到 Cloudflare Pages

1. https://dash.cloudflare.com -> `Workers & Pages` -> `Create` -> `Pages` -> `Connect to Git`
2. 选择仓库并填：

| 字段 | 值 |
| --- | --- |
| Framework preset | Vite |
| Root directory | `frontend` |
| Build command | `npm install && npm run build` |
| Build output | `dist` |
| 环境变量 | `VITE_API_BASE_URL = https://ai-world-api.onrender.com/api` |

3. 部署后把 `https://xxx.pages.dev` 回填到 Render 的 `CORS_ORIGINS`（逗号分隔，两个前端都写）

### B3. 验证（部署完把域名发我，我从公网实测）

- 打开 Pages 域名 -> 应看到**登录/注册页** -> 注册一个账号
- 上传文件 / 提问 / 圆桌 -> 与 Streamlit 版是**同一份数据**
- 浏览器**返回键**应能在页面间后退（React 有真正的路由）

---

## 常见问题

**Q：为什么本地 127.0.0.1 别人打不开？**
回环地址只指向本机。局域网要用 `http://<你的局域网IP>:5173`，公网要用上面的托管方案。

**Q：免费会不会突然收费？**
A 和 B 的免费档都不需要信用卡，不会自动扣费。Render 免费档会休眠；Streamlit Cloud 长时间无人访问也会休眠（访问时自动唤醒）。

**Q：数据会不会丢？**
托管平台的文件系统是临时的，重新部署/重启会重置 SQLite。演示够用；要持久就接云 Postgres（代码已支持 `DATABASE_URL`）。

**Q：怎么防止别人刷我的 API 额度？**

1) **最好的办法：干脆不配站点级 Key** —— 应用内建了**访客自带 Key（BYOK）**：
   侧边栏「🔑 使用我自己的 API Key」，访客用他自己的额度，站点零成本、零风险。
2) Streamlit 方案设 `APP_PASSWORD`（只给受邀者用）；
3) 后端已内置按 IP 限流；
4) 演示站点可以只跑零成本离线模式。

> 注意：访客的 Key 会经过服务端才能调用模型（服务端代理型应用的固有特性）。
> 代码已确保不落库、不记日志、不回显；但站长的服务器在技术上仍可能观察到它。
