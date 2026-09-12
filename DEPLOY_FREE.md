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
# 可选：配置后就是真实 DeepSeek 回答
DEEPSEEK_API_KEY = "sk-xxxx"
```

保存后应用会自动重启，进去就需要口令。

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
