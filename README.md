# AI World · AI 世界

> 一个个人 / 企业级 AI 智能空间系统：创建自己的 **AI 伙伴**、**知识世界** 与 **多 Agent 协作团队**。

本项目是一个**完整可运行的工程**（不是代码片段），包含 React 前端、FastAPI 后端、SQLite 数据库、RAG 检索与多 Agent 圆桌。

**在线演示（永久域名，电脑关机也能访问）：** https://aiworld.streamlit.app

---

## 一、功能一览（Phase 1 已全部完成）

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 首页 Dashboard | 完成 | 卡片式总览：伙伴数、知识库数、向量切片数、运行模式、模块入口 |
| AI 伙伴 | 完成 | 创建/删除角色：名称、身份、人格、领域、说话方式、System Prompt |
| 知识世界 | 完成 | 上传 PDF / DOCX / TXT / MD -> 解析 -> 切片 -> 向量化 -> 入库 |
| AI 聊天 | 完成 | 选择伙伴提问 -> 检索知识库 -> 拼接 Prompt -> DeepSeek -> 回答并标注来源 |
| RAG 基础 | 完成 | 独立模块 `app/rag/`：loader / splitter / embedding / retriever / rag_service |
| AI 圆桌 | 完成 | 主持 Agent 拆解议题 -> 多专家 Agent 并行发言 -> 主持 Agent 汇总最终方案 |
| 用户系统 | 完成 | 注册 / 登录 / JWT / 数据隔离（Phase 2 提前落地） |
| 记忆系统 | 完成 | Memory 表 + `/api/memories` 接口（Phase 2 接口已可用） |
| Docker | 完成 | 前后端 Dockerfile + docker-compose.yml，一条命令启动 |

> **离线演示模式**：如果未配置 `DEEPSEEK_API_KEY`，`/api/chat` 与 `/api/roundtable` 会返回明确标注的本地占位回答，
> 保证项目在**没有任何外部依赖的情况下也能完整跑通**；填入 Key 后即为真实模型回答。

---

## 二、技术栈

**前端**：React 18 + Vite 5 + Tailwind CSS 3 + React Router 6 + Axios（现代化 SaaS 风格）
**后端**：Python 3.10+ / FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2 + PyJWT + httpx
**数据库**：SQLite 第一版（通过 `DATABASE_URL` 可平滑迁移到 PostgreSQL）
**模型**：DeepSeek API（OpenAI 兼容协议），Key 只存在于服务端 `.env`

### 后端分层（严格单向依赖）

```
HTTP 请求
   |
   v
API 路由 (app/api/routes)            <- 只做参数校验、鉴权、调用 Service
   |
   v
Service 业务层 (app/services)        <- 业务规则、事务边界、数据隔离；绝不写 SQL
   |
   v
Repository 数据层 (app/repositories) <- 唯一出现 SQLAlchemy 查询的地方
   |
   v
Database (app/database)              <- 异步引擎 / 会话 / 建表
```

AI 相关能力单独分包：`app/ai/`（DeepSeek 客户端、Prompt、离线兜底）与 `app/rag/`（检索增强生成）。

---

## 三、目录结构

```
AI_World/
├── frontend/                     # React + Vite + Tailwind
│   ├── src/
│   │   ├── api/                  # client.js (axios + JWT 拦截器), endpoints.js
│   │   ├── components/           # Layout / Sidebar / Modal / ui / MarkdownText
│   │   ├── context/              # AuthContext (登录态)
│   │   ├── pages/                # Dashboard / Characters / Knowledge / Chat / Roundtable / Login
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── Dockerfile, nginx.conf, vite.config.js, tailwind.config.js
│   └── package.json
│
├── backend/                      # FastAPI
│   ├── app/
│   │   ├── main.py               # 应用入口 / 中间件 / 全局异常
│   │   ├── api/                  # deps.py + routes/ (auth, characters, knowledge, chat, roundtable, memory)
│   │   ├── services/             # 业务逻辑（不含 SQL）
│   │   ├── repositories/         # 数据访问（唯一 SQL 层）
│   │   ├── models/               # ORM: User/Character/KnowledgeBase/Document/Conversation/Message/Memory
│   │   ├── schemas/              # Pydantic 请求响应模型
│   │   ├── ai/                   # deepseek_client.py / prompts.py / offline.py
│   │   ├── rag/                  # loader.py / splitter.py / embedding.py / retriever.py / rag_service.py
│   │   ├── database/             # base.py / session.py / init_db.py
│   │   ├── core/                 # security.py / errors.py / files.py / ratelimit.py
│   │   └── config/               # settings.py (.env 读取)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── data/                         # SQLite 数据库 + 上传的原始文件
├── docker-compose.yml
├── package.json                  # 根脚本（可选，一键并行启动）
├── start.sh / start.bat          # 本地一键启动脚本
├── requirements.txt
├── .env.example
└── README.md
```

---

## 四、快速开始

### 方式 A：本地运行（推荐先试这个）

**1. 准备环境变量**

```
cd AI_World
cp .env.example .env        # Windows: copy .env.example .env
```

编辑 `.env`，填入 `DEEPSEEK_API_KEY`（不填也能跑，会进入离线演示模式）。

**2. 启动后端**

```
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

后端地址：http://127.0.0.1:8000 ，交互式文档：http://127.0.0.1:8000/docs

**3. 启动前端**（另开一个终端）

```
cd frontend
npm install
npm run dev
```

前端地址：http://127.0.0.1:5173

**4. 一键启动（可选）**

```
./start.sh        # macOS / Linux
start.bat         # Windows
```

或使用根目录脚本并行启动（需要 Node）：

```
npm install
npm run dev
```

### 方式 B：Docker 一条命令

```
cd AI_World
cp .env.example .env        # 必须先有 .env，docker compose 会读取它
docker compose up --build
```

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000/docs
- 数据库与上传文件持久化在 `./data`

---

### 方式 C：免费部署到公网（永久域名，电脑可关机）

详细步骤见 `DEPLOY_FREE.md`，两条免费路线（均已实测国内可直连）：

| 路线 | 得到什么 | 界面 |
| --- | --- | --- |
| Streamlit Community Cloud | `https://xxx.streamlit.app` | Streamlit 演示版（`streamlit_app.py`，复用同一套后端 Service 层） |
| Cloudflare Pages + Render | 前端 `pages.dev` + 后端 `onrender.com` | React 原版 |

提示：仓库根目录的 `requirements.txt` 是 Streamlit Cloud 使用的完整依赖；后端单独部署用 `backend/requirements.txt`。

---

## 五、环境变量说明（.env）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API Key，**只放服务端**，绝不写进前端代码 |
| `DEEPSEEK_BASE_URL` | https://api.deepseek.com | 兼容 OpenAI 协议的服务地址 |
| `DEEPSEEK_MODEL` | deepseek-v4-flash | 模型名。**deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用**，可选 `deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-v4-flash-vision-exp` |
| `EMBEDDING_PROVIDER` | local | `local` = 内置离线哈希向量；`openai` = 调用外部向量服务 |
| `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | 空 / 空 / BAAI/bge-m3 | 外部向量服务配置 |
| `EMBEDDING_DIM` | 512 | 本地向量维度（换向量方案需重新入库） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 600 / 100 | 文本切片长度与重叠 |
| `RETRIEVAL_TOP_K` | 4 | 每次检索返回的片段数 |
| `DATABASE_URL` | 空 | 空 = SQLite；可填 PostgreSQL 异步 URL |
| `JWT_SECRET` | dev 占位值 | **生产必须替换为随机长字符串** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 10080 | Token 有效期（7 天） |
| `ALLOWED_EXTENSIONS` | .pdf,.txt,.md,.docx | 允许上传的扩展名白名单 |
| `MAX_UPLOAD_MB` | 20 | 单文件大小上限 |
| `CORS_ORIGINS` | localhost:5173 等 | 允许的前端来源 |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | demo / demo123 | 演示账号 |
| `ADMIN_USERNAMES` | 空 | 逗号分隔的管理员用户名；配置后启用 `/api/admin/*`（权限系统） |
| `SUMMARY_ENABLED` | true | 滚动摘要开关 |
| `HISTORY_RETRIEVAL_ENABLED` / `HISTORY_RETRIEVAL_TOP_K` | true / 3 | 历史向量检索开关与召回条数 |
| `MEMORY_AUTO_EXTRACT` / `MEMORY_EXTRACT_EVERY` | true / 6 | 自动事实抽取开关与触发频率 |

---

### 五·一、权限系统（文档 Phase 2）

- `users.role`（`user` / `admin`）+ 环境变量 `ADMIN_USERNAMES`。**默认没有任何管理员**。
- 管理员专属接口：`GET /api/admin/usage`（全站用量）、`GET /api/admin/usage/recent`（调用明细）、
  `GET /api/admin/users`（用户列表）。非管理员访问返回 **403**，未登录返回 **401**。
- 数据隔离是另一层：所有业务查询强制带 `user_id`，与角色无关。

---

## 六、测试账号与验证方法

**测试账号**

| 账号 | 密码 | 说明 |
| --- | --- | --- |
| `demo` | `demo123` | 登录页「一键体验 Demo」按钮自动创建/登录，并预置 3 个 AI 伙伴 |

也可以用注册接口创建自己的账号（数据与 demo 完全隔离）。

**接口自测（curl）**

```
# 1) 健康检查
curl http://127.0.0.1:8000/api/health

# 2) 演示账号登录，拿 Token
curl -X POST http://127.0.0.1:8000/api/auth/demo-login

# 3) 带上 Token 调用（把 TOKEN 替换为上面的 access_token）
curl -H "Authorization: Bearer TOKEN" http://127.0.0.1:8000/api/characters

# 4) 上传知识文件
curl -H "Authorization: Bearer TOKEN" -F "file=@README.md" -F "name=项目说明" http://127.0.0.1:8000/api/knowledge/upload

# 5) 基于知识库提问
curl -X POST http://127.0.0.1:8000/api/chat -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d "{\"character_id\": 1, \"message\": \"这份资料讲了什么？\"}"

# 6) 发起 AI 圆桌
curl -X POST http://127.0.0.1:8000/api/roundtable -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"如何开发一款医疗 AI 产品？\"}"
```

**端到端自动测试（推荐）**

```
cd backend
python tests/smoke_test.py
```

覆盖 29 项断言：健康检查、注册/登录、鉴权、AI 伙伴 CRUD、知识库上传与向量化、RAG 检索、
AI 圆桌、记忆系统、跨用户数据隔离、上传安全（扩展名 / 文件签名 / 路径穿越）。

**界面自测路径**

1. 打开 http://127.0.0.1:5173 -> 点击「一键体验 Demo」
2. 「AI伙伴」-> 新建一个角色
3. 「知识世界」-> 上传一个 PDF/TXT/MD -> 查看切片
4. 「AI聊天」-> 选择角色 + 知识库 -> 提问，观察「引用来源」标签
5. 「AI圆桌」-> 输入议题 -> 发起讨论，查看各 Agent 发言与主持人汇总

---

## 七、API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | 注册并返回 JWT |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/demo-login` | 演示账号一键登录 |
| GET | `/api/auth/me` | 当前用户 |
| GET / POST | `/api/characters` | AI 伙伴列表 / 创建 |
| GET / PUT / DELETE | `/api/characters/{id}` | 详情 / 更新 / 删除 |
| GET / POST | `/api/knowledge` | 知识库列表 / 创建 |
| POST | `/api/knowledge/upload` | 上传文件并向量化 |
| GET | `/api/knowledge/{id}/documents` | 切片列表 |
| DELETE | `/api/knowledge/{id}` | 删除知识库及切片 |
| POST | `/api/chat` | 对话（自动 RAG） |
| GET | `/api/chat/history` | 聊天记录 |
| GET | `/api/chat/conversations` | 会话列表 |
| GET | `/api/roundtable/agents` | 可选与会 Agent |
| POST | `/api/roundtable` | 发起圆桌讨论 |
| GET / POST / DELETE | `/api/memories` | 记忆系统接口 |
| GET | `/api/health` 、 `/api/meta` | 健康检查 / 运行信息 |

---

## 八、RAG 流程

```
上传文件
  |  loader.py      PDF/DOCX/TXT/MD -> 纯文本（pypdf / python-docx）
  v
文本切片
  |  splitter.py    段落 -> 句子 -> 硬切分，带 overlap
  v
向量化
  |  embedding.py   local（离线哈希向量）或 openai 兼容接口
  v
向量入库
  |  documents 表，embedding 以 JSON 文本保存（未来可换 pgvector）
  v
检索
  |  retriever.py   余弦相似度 Top-K，限定在当前用户的知识库范围
  v
上下文拼接 -> Prompt -> DeepSeek -> 回答 + 来源标注
```

---

### 八·一、文本切片（不是用向量切的）

切片用的是「规则 + 语言边界」的递归切分，和向量无关：

1. **规范化**：统一换行、压缩空白
2. **段落切分**：按空行分段
3. **句子切分**：段内超过 `CHUNK_SIZE` 时，按 。！？；; . 断句
4. **硬切分**：单句仍然超长时，按字符数切开
5. **重叠**：相邻切片保留 `CHUNK_OVERLAP`（默认 100 字符）尾部，避免跨切片语义断裂

可调参数：`CHUNK_SIZE`（默认 600）、`CHUNK_OVERLAP`（默认 100）。
升级方向：Markdown 标题感知切分、语义切分（按相邻句子向量距离突变处切）、父子块（small-to-big）。

### 八·二、向量与检索

- **向量** = 把一段文本变成一串数字（默认 512 维），检索时用**余弦相似度**取 Top-K（`RETRIEVAL_TOP_K`，默认 4）。
- 默认 `EMBEDDING_PROVIDER=local`：按字/词做哈希映射 + L2 归一化。
  零依赖、可离线、零成本，但**本质是关键词重合度，不是语义模型**
  （例如「心梗」和「心肌梗死」可能匹配不上）。
- 想要真正的语义检索：`EMBEDDING_PROVIDER=openai` + `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL`（如 BGE-M3）。
  > **事实**：DeepSeek 官方**没有** embedding 接口，所以向量化只能用第三方兼容服务或本地实现。
- 更换向量模型后需要**重新上传文件**（旧向量与新向量不在同一空间，无法比较）。
- **界面自带入口**：Streamlit 侧边栏「🧠 使用我自己的向量服务」可直接填 API Base / Key / 模型，
  只在该访客会话内生效（不入库、不写日志、不共享）；未填写则回落到服务端配置或内置离线实现。

### 八·三、长记忆三件套（已实现）

每次请求的 prompt 由这几部分拼成，每一部分都有明确预算，不会无限膨胀：

| 组成 | 机制 | 参数 |
| --- | --- | --- |
| 角色设定 | 身份 / 人格 / 专长 / System Prompt | — |
| 长期事实记忆 | 角色专属 + 全局记忆注入 system prompt | `MEMORY_INJECT_LIMIT`（5） |
| 滚动摘要 | 滑出窗口的旧消息压缩成摘要，存 `conversations.summary` | `SUMMARY_ENABLED` |
| 近端窗口 | 最近 N 条 + 字符预算 | `HISTORY_LIMIT`（12）/ `HISTORY_CHAR_BUDGET`（6000） |
| 历史向量召回 | 窗口之外的旧消息按余弦相似度召回 Top-K | `HISTORY_RETRIEVAL_TOP_K`（3） |
| 知识库片段 | 文档 Top-K 片段 | `RETRIEVAL_TOP_K` / `MAX_CONTEXT_CHARS` |

**自动事实抽取**：每 `MEMORY_EXTRACT_EVERY`（6）条消息，让模型抽取 0~3 条用户事实/偏好写入 `memories` 表；
`MEMORY_AUTO_EXTRACT=false` 可关闭（需要可用的模型 Key，离线演示模式会自动跳过）。

**仍然存在的限制（如实说明）**：

- 摘要上限 4000 字符，极长对话仍会丢细节
- 历史召回的精度取决于向量质量：内置离线哈希向量只能按关键词重合召回
- 摘要与自动抽取各会多消耗一次模型调用

**验证**：`backend/tests/context_test.py`（16 项，用假 AI 客户端确定性验证三件套）

### 八·四、多 Agent RAG（可选深度模式）

默认关闭，界面上勾选「🔬 多 Agent 深度检索」或接口传 `"rag_mode": "multi"` 才启用；
**默认单轮 RAG 行为完全不变**。

```
用户问题
  ↓  问题拆解 Agent    拆成 1-4 个可独立检索的子问题（上限 `MULTI_AGENT_MAX_ROUNDS` 相关）
  ↓  资料查找 Agent    原问题 + 每个子问题各检索一次，按 document_id 去重
  ↓  证据审查 Agent    判定证据是否充分/一致；不足则提出补充检索
  ↓  （有界循环）       最多 `MULTI_AGENT_MAX_ROUNDS` 轮补检，避免 token 失控
  ↓  答案整理 Agent    综合证据 + 审查意见，生成带引用编号的回答
```

- 代码位置：`backend/app/rag/multi_agent.py`（保持 RAG 包自包含）
- 协商过程会展示在界面的「多 Agent 协作过程」折叠面板里（子问题 / 审查结论 / 轮次）
- 离线演示模式下每个阶段都有确定性兜底，流程照样跑通
- 验证：`backend/tests/multi_agent_test.py`（19 项：执行顺序、检索覆盖、有界补检、离线降级、解析健壮性）

## 九、安全与可信设计

这一版把「安全、可靠、值得信任」当作硬要求，具体落实如下：

**1. 密钥与配置**

- `DEEPSEEK_API_KEY`、`JWT_SECRET` 等只从环境变量 / `.env` 读取，代码中没有任何硬编码密钥。
- `.env` 已加入 `.gitignore`；对外只提供 `.env.example`。
- 启动时若检测到 `JWT_SECRET` 仍是默认值、或未配置 API Key，会在日志中明确告警。

**2. 身份与数据隔离**

- 全站 JWT 鉴权，聊天、知识库等全部接口都必须携带 Token。
- 每一个数据访问都带 `user_id`：伙伴、知识库、切片、会话、消息、记忆都属于某个用户，跨用户访问直接 404/403。
- 密码使用 PBKDF2-HMAC-SHA256（240000 次迭代 + 随机盐）存储，绝不明文保存。

**3. 文件上传安全**

- 扩展名白名单：仅 `.pdf / .txt / .md / .docx`。
- 大小限制：默认 20MB（`MAX_UPLOAD_MB`）。
- 文件签名校验：伪装成 PDF/DOCX 的可执行文件会被拒绝。
- 文件名净化：剥离路径与危险字符，杜绝路径穿越；磁盘上再叠加随机前缀。

**4. 接口与错误处理**

- 所有业务错误走统一的 `ServiceError` -> 结构化 JSON，不把堆栈、SQL、内部路径返回给前端。
- 登录 / 上传 / AI 调用带进程内限流（429），防止暴力破解与请求风暴。
- 响应统一加 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Cache-Control: no-store`。
- CORS 白名单化；数据库访问全部通过 ORM 参数化查询，不存在字符串拼接 SQL。

**5. 可靠性与可观测**

- DeepSeek 调用有超时、错误分类与明确提示；调用失败返回 502，而不是静默返回假数据。
- 未配置 Key 时进入「离线演示模式」，并在回答与界面上明确标注，不伪装成真实模型输出。
- `/api/health` 健康检查 + Docker HEALTHCHECK；SQLAlchemy `pool_pre_ping` 自动剔除失效连接。
- 事务边界在 Service 层统一 commit，异常自动回滚。
- **数据库自动备份**：每次启动先把 SQLite 复制到 `data/backups/`（保留最近 `BACKUP_KEEP` 份，纯本地零成本）；
  结构迁移只做 ADD COLUMN，绝不删列/删表。
- **消息默认永久保留**：对话消息全部落库（`messages` 表），不会自动清理，也不受长记忆窗口影响
  （窗口只影响送进模型的上下文，不改数据库）。
  唯一会丢数据的情况是平台文件系统被重置 —— 托管平台的容器是临时的，
  要跨重启长期保留，把 `DATABASE_URL` 指向云数据库（Neon / Supabase 免费档）即可，代码无需改动。

**6. API Key 与访客自带 Key（BYOK）**

- 站点级 Key 只从服务端环境变量 / Secrets 读取，**绝不返回给前端**。
- 应用支持访客在侧边栏填入**自己的** API Key：该 Key 只保存在当前浏览器会话内存（`st.session_state`），
  **不写入数据库、不写日志、不与其他访客共享**，清空即失效。
- 因此可以做到：站点**不配置任何 Key**（零成本、零泄露风险），仍让访客用自己的额度体验真实模型；
  并且支持任意兼容 OpenAI 协议的服务（可自定义 API Base 与模型名，不限于 DeepSeek）。
- 诚实说明：访客的 Key 必须经过服务端才能调用模型（这是所有「服务端代理型」应用的固有特性），
  因此**站长的服务器在技术上可以观察到该 Key**；代码层面已确保不落库、不记日志、不回显。
  如果不接受这一点，就不要在自己的站点开放 BYOK。

**7. 用量审计（管理员可见）**

- 记录每次操作的**元数据**：时间、匿名会话标识、IP（平台提供时）、功能、模型、
  是否自带 Key、成功 / 失败、耗时、回答长度。
- **绝不记录 API Key，也不记录对话内容**（对话内容本身已存在于会话表 `messages` 中）。
- 站点管理员在 Secrets 配置 `APP_ADMIN_PASSWORD` 后，侧边栏会出现「📊 用量统计」页面
  （调用次数、独立访客数、自带 Key 次数、失败次数、平均耗时、最近 50 条明细）；
  **未配置时该页面对所有访客隐藏**。

**需要你明确授权的部分（我不会擅自操作）**

- 涉及**你的真实 API Key、账号密码、个人信息**的任何写入行为；
- 任何**对外暴露服务**（部署到公网、开放端口、绑定域名）；
- 任何**读取或修改工作区之外的文件 / 数据**。

请把信息提供给我，或由你自己填入 `.env`。

---

## 十、性能与扩展

- **异步全链路**：FastAPI + SQLAlchemy async + httpx，AI 调用不阻塞事件循环。
- **前端**：Vite 构建、页面级组件拆分、axios 统一封装。
- **水平扩展预留**：Service 层与 Repository 层完全解耦，业务代码里没有任何 SQL。
- **任务队列**：大文件批处理 / 长任务可在 `RagService.ingest` 之外接入 Celery。
- **向量库**：当前为 SQLite + JSON 向量 + Python 余弦相似度，适合 100 用户量级；数据量大时换 pgvector / Milvus，只需改 Repository 与 embedding 存储列。

### 迁移到 PostgreSQL

```
pip install asyncpg
# .env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ai_world
```

重新启动后端即可（表结构由 SQLAlchemy 自动创建；生产环境建议改用 Alembic 迁移）。

---

## 十一、常见问题

**Q：不配置 DeepSeek Key 能用吗？**
能。所有页面和流程都能跑通，AI 回答会标注「离线演示模式」。

**Q：为什么回答和知识库关系不大？**
默认向量是离线哈希向量（零依赖、可离线），语义能力有限。把 `EMBEDDING_PROVIDER` 换成 `openai` 并配置 BGE-M3 之类的服务，检索质量会明显提升（换模型后需要重新上传文件）。详见「八·一 ~ 八·三」。

**Q：聊久了 AI 会忘记前面说的？**
会。上下文是「最近 12 条消息 / 6000 字符预算」的固定窗口，超出即丢弃（详见「八·三」）。要跨会话记住事实，请调用 `POST /api/memories` 写入记忆。

**Q：PDF 上传提示「未提取到文本」？**
扫描版 PDF 没有文本层，需要先做 OCR。

**Q：用 Python 脚本自测接口时报 502 空响应？**
本机开着系统代理（例如 Clash 的 `127.0.0.1:57777`）时，`httpx` / `requests` 会把 `127.0.0.1` 的请求也送进代理，返回 502。
测试脚本已用 `trust_env=False` 规避；自己的脚本加同样参数，或把 `127.0.0.1` 加入代理白名单。

**Q：端口被占用？**
后端：`uvicorn app.main:app --port 8001`；前端：修改 `frontend/vite.config.js` 的 `server.port`，并同步 `VITE_API_TARGET`。

**Q：数据库文件在哪？**
`AI_World/data/database.db`（删除即可重置全部数据）。

---

## 十二、开发计划状态

- **Phase 1（已完成）**：React 网页、FastAPI、SQLite、AI 伙伴、AI 聊天、知识库上传、RAG 基础、AI 圆桌。
- **Phase 2（大部分已完成）**：用户系统 / JWT / 数据隔离 / 记忆表与接口已落地；SSO、更细粒度权限待补。
- **Phase 3（骨架已就绪）**：Docker 一键部署已完成；PostgreSQL、Redis、Celery、对象存储待按需接入。

---

## 十三、许可证

仅用于学习与内部项目演示。接入 DeepSeek 等第三方服务时，请遵守对应服务条款与数据合规要求。
