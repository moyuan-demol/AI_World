# AI World · AI 世界

> 一个个人 / 企业级 AI 智能空间系统：创建自己的 **AI 伙伴**、**知识世界** 与 **多 Agent 协作团队**。

本项目是一个**完整可运行的工程**（不是代码片段），包含 React 前端、FastAPI 后端、SQLite 数据库、RAG 检索与多 Agent 圆桌。

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
| `DEEPSEEK_MODEL` | deepseek-chat | 模型名 |
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
默认向量是离线哈希向量（零依赖、可离线），语义能力有限。把 `EMBEDDING_PROVIDER` 换成 `openai` 并配置 BGE-M3 之类的服务，检索质量会明显提升（换模型后需要重新上传文件）。

**Q：PDF 上传提示「未提取到文本」？**
扫描版 PDF 没有文本层，需要先做 OCR。

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
