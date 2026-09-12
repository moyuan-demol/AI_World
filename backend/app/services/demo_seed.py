"""预置公共示例库（版本化幂等种子数据）。

为什么要有它：
- 新用户第一次进来往往没有资料，检索链路"看起来没效果"；公共示例库让任何人都能
  立刻验证「检索是否命中」；
- 文档里写死了若干**唯一、可对照**的事实点（0.18 / RRF k=60 / 2 条切片 …），
  既可以人工提问验证，也被自动化测试当成断言目标。

版本化（为什么不能只靠"存在即跳过"）：
- 旧实现"同名文档存在就跳过"，导致扩写后的手册**永远不会更新到已部署的线上库**；
- 现在给手册加一个"版本指纹"（PUBLIC_DOC_VERSION + 正文内容的哈希），随正文一起
  写进切片。ensure_public_demo() 读到的是旧指纹时，会先物理删除旧切片再重新切片
  入库 —— 于是"改内容"和"只改 PUBLIC_DOC_VERSION"都能在下次启动时自动生效；
- 指纹一致则跳过，连续调用两次不会重复插入、不会重复切片。

回收站行为（明确、可测试）：
- 公共知识库本身在回收站 -> 整体跳过（skipped_reason="knowledge_recycled"），
  绝不"复活"被有意删除的内容，恢复应由站长在回收站显式执行；
- 手册切片全部在回收站 -> 同样跳过（skipped_reason="document_recycled"）；
  恢复之后若指纹过旧，下一次调用会自动重建为最新版本。

调用位置：后端启动（app.main.lifespan）与 Streamlit bootstrap（首次访问）。
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.files import sanitize_filename
from app.core.security import hash_password
from app.models.user import User
from app.rag.embedding import EmbeddingConfig
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.user_repository import UserRepository
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

PUBLIC_KB_NAME = "🌍 公共示例库（所有人可查）"
# 用户明确要求删除公共库描述文案，因此这里留空；
# Streamlit 的公共库区块也不再渲染 description（见 streamlit_app.page_knowledge）。
PUBLIC_KB_DESCRIPTION = ""
PUBLIC_DOC_FILENAME = "AI 世界 · 使用手册与检索测试题.md"
# 上传链路会做文件名净化（空格 / "·" 等不安全字符 -> "_"），真正入库的是净化后的名字。
# 幂等判重必须用这个名字，否则每次启动都会把同一篇文档再插一遍。
PUBLIC_DOC_STORED_FILENAME = sanitize_filename(PUBLIC_DOC_FILENAME)

# 手册版本号：只要"内容变了"或"只改这个常量"，下次 ensure_public_demo() 都会重建切片。
PUBLIC_DOC_VERSION = 2

# 正文约 5500 字：覆盖 5 个模块、25 条可验证事实、5 道测试题与 1 段干扰内容。
# 为什么写这么长：公共库要能撑起"多切片召回 + 相邻合并 + 同文档上限"等链路演示，
# 旧版只有 3 条切片，演示与自测都不够用。
PUBLIC_DOC_CONTENT = """# AI 世界 · 使用手册与检索测试题

> 本文档是 **公共示例文档**，用于演示与验证知识库检索的准确性，任何账号的检索都会自动包含它。
> 你可以直接提问文中的任意事实点，检查系统是否命中下面的「可验证事实点清单」。
> 文档不含任何隐私数据，可放心引用与转述。

## 一、系统概览：五个模块

1. **我的世界**：首页总览。集中展示 AI 伙伴、知识库、向量切片与运行模式的实时统计，让你一眼看清"当前有多少资料可被检索"。首次进入时会自动补齐演示账号与预置角色，方便快速体验，不需要手工准备数据。
2. **AI伙伴**：定义角色的身份、人格、专长与说话方式。每个角色都能划定专属知识边界，回答时只检索你允许的资料，避免不同领域的知识互相串味。角色设定会作为系统提示词注入模型，让语气与专业度保持一致。
3. **知识世界**：上传资料后自动解析、切片、向量化，检索时按相似度召回并标注来源文件。支持分级目录，文件夹、子库、子子库可以任意嵌套，删除父级时整棵子树一并进入回收站。你也可以在这里查看任意切片，核对检索依据。
4. **AI聊天**：先检索知识库（可选开启联网检索），再把相关片段与问题一起交给模型作答。未配置模型 Key 时会进入离线演示模式，仍然把检索到的原文句子按文档分组展示，让你在不联网的情况下也能确认检索是否正确。
5. **AI圆桌**：主持人 Agent 先拆解议题，多个专家 Agent 并行发言，最后由主持人汇总成结构化成稿，适合方案评审、产品立项与头脑风暴。圆桌与单轮聊天完全隔离，不会互相污染上下文。

## 二、角色与知识边界

给角色绑定知识库或文件夹，可以让它只在自己的专业范围内回答。绑定文件夹时会**自动包含其下整棵子树**：绑定一个"医学资料"文件夹，里面的子库、子子库都会被检索到，不需要逐个勾选，这解决了资料越分越细、边界难以维护的问题。
没有绑定任何知识库的角色，默认检索"我的全部知识库 + 全部公共库"；别人的私有资料永远不可见。用户、角色、知识库之间靠账号隔离，任何检索都会先按"可见知识库 id 集合"收窄范围，再进入向量与 BM25 通道。
绑定关系保存在角色与知识库的关联表里，保存后立即生效，无需重启服务。公共库不属于任何个人账号，由站长统一维护，但所有登录用户都能检索到它。

## 三、知识库分级与文件支持

知识库支持三级以上的分级结构：文件夹可以包含子库，子库还可以继续包含子子库。检索与删除都以"整棵子树"为单位，绑定文件夹即自动包含子树，删除父级时子级与切片一并进入回收站。
文件解析器目前支持 **40 种文件格式**，按用途可以分为三类：
- 办公文档类 10 种：.pdf、.docx、.pptx、.xlsx、.xlsm、.xls、.rtf、.odt、.ods、.odp；
- 数据与配置类 11 种：.csv、.json、.xml、.yaml、.yml、.ini、.cfg、.conf、.toml、.log、.sql；
- 代码与文本类 19 种：.txt、.md、.markdown、.text、.html、.htm、.py、.js、.ts、.java、.c、.h、.cpp、.go、.rs、.rb、.php、.vue、.css。
上传时有三道安全闸门：第一道是**扩展名白名单**，不在允许列表里的后缀直接拒绝；第二道是**内容签名校验**，PDF 必须包含 %PDF 头，Office 与 OpenDocument 必须包含 ZIP 的 PK 头，XLS 必须是 OLE 复合文档头，文本文件里出现二进制字节也会被拒绝，防止把可执行文件改名成文档骗过服务器；第三道是**文件名净化**，去掉目录分隔符与控制字符，防止目录穿越。单个文件默认不超过 20 MB，避免一次上传拖垮向量化。

## 四、检索原理：混合检索 + 精排

检索不是"一次向量比对"就结束，而是一条可解释的流水线：
1. **稠密向量通道**：把问题与切片都编码成向量，用余弦相似度找"语义相近"的内容。默认使用内置的离线哈希实现（512 维、零依赖、不是语义模型），配置外部向量服务后会切换成真正的语义向量。
2. **BM25 稀疏通道**：对"阿柏西普""VEGF""CRVO"这类精确术语、缩写与专有名词命中极准，弥补向量对精确词不敏感的短板。BM25 使用标准参数 k1=1.5、b=0.75。
3. **RRF 融合**：两路结果按**排名**而不是分数融合，公式为 1/(k+rank)，常数 k=60，因此余弦 0~1 与 BM25 0~n 两种量纲可以安全合并。RRF 常数为 60。
4. **最低相关性阈值**：向量通道低于 **0.18** 的切片直接丢弃，不进入候选，避免"沾边就列"。
5. **相邻切片合并**：同一文档里 chunk_index 相差不超过 1 的命中切片会拼成一段连贯上下文，减少割裂与 token 浪费。
6. **同文档上限**：同一篇文档在最终结果里最多保留 **2 条**切片，防止一篇长文档吃光全部名额，让多篇文档都有机会进入上下文。
7. **Rerank 精排**：用三个可解释因子重排候选——覆盖率占 0.55（命中的实词种类比例）、命中位置占 0.20（越靠前越关键）、短语命中占 0.25（查询原话出现是强相关信号），权重之和为 1.0。
切片默认每 600 字一块、相邻块重叠 100 字，最终只把排名前 4 条、总长度不超过 6000 字的片段送入模型上下文。元信息问句还会额外补入每篇文档的最前面 1 条切片，因为作者、单位、期刊这类事实几乎总在第一页。

## 五、长记忆三件套

为了避免"聊到后面忘了前面"，系统准备了三种互补的长期记忆机制：
- **滚动摘要**：滑出对话窗口的旧消息会被压缩成一段摘要，随会话持久化并注入系统提示词，保留用户身份、偏好、目标与未完成事项。
- **历史向量检索**：在窗口之外的历史消息里按相关度召回，默认看最近 200 条、取最相关的 3 条，把"很久以前说过的相关事实"重新带回当前这轮对话。
- **自动事实抽取**：每 6 条消息触发一次，从对话中提取身份、偏好、项目等长期事实写入记忆表，之后按最近写入取有限条注入系统提示词，避免每次都靠模型记忆。
需要说明的是，近端历史仍然是"固定窗口 + 字符预算"；真正要长期记住的事实，请以记忆表为准。三者分工明确：摘要管"脉络"，历史检索管"细节"，事实抽取管"稳定偏好"。

## 六、联网检索（外部世界接口）

开启联网后，系统可以并行查询 5 个来源：**维基百科、DuckDuckGo、SearXNG、Tavily、Serper**。每个来源有独立超时（默认 8 秒），单源最多取 3 条、总数最多 6 条，低于 0.12 相关性的网页会被丢弃。
某个来源失败只会被记录在报告里，不会阻塞其它来源，也不会影响知识库回答——这就是"失败降级"。网页结果会被包装成伪切片（document_id 为负数）与知识库片段一起进入上下文和引用列表。
有一条例外：**元信息类问题**（作者、页数、上传时间、文件名）会跳过联网检索，只在知识库内求证，因为这类事实属于用户自己上传的文档，互联网上根本搜不到，联网只会带回无关词条。这条规则保持不变。联网结果与知识库结果共用同一份"来源"展示，便于逐条核对。

## 七、多 Agent 圆桌

圆桌会把一个复杂议题拆成若干子问题，按四步推进：**问题拆解 → 资料查找 → 证据审查 → 答案整理**。主持人先拆解议题，各专家 Agent 依据角色设定与检索到的证据发言，审查环节会检查证据是否充分并在有界轮次内补检（默认最多 2 轮，每个子问题取 4 条证据），最后汇总成结论、方案、风险与行动清单。
圆桌的检索与单轮聊天共用同一套知识边界与混合检索链路，因此"角色绑定文件夹自动包含子树"等规则在圆桌上同样生效。所有 Agent 的发言都会随会话落库，方便回看。

## 八、回收站（软删除 / 恢复 / 彻底删除）

删除知识库或文档时执行的是**软删除**：只写入删除时间，物理记录仍然存在。回收站可以列出删除条目并原样**恢复**；只有显式**彻底删除**才会物理清空。
权限规则：普通用户只能彻底删除自己删除的私有内容；公共库内容任何人可以软删除或恢复，但**彻底删除只有站长可以执行**，避免误操作让所有用户失去公共示例库。站长的判定来自 ADMIN_USERNAMES 环境变量与账号 role 字段的组合。
删除父级知识库时，整棵子树与其中的切片一起进入回收站；恢复父级时子级一并恢复；彻底删除父级则整棵子树物理清空。恢复文档时按"知识库 + 文件名"整篇恢复，不会只回来一半切片。

## 九、数据与安全

- **账号隔离**：每个用户的会话、记忆、知识库、文档都带 user_id，检索前先解析可见范围，别人的私有资料既检索不到，也读不到、删不掉。
- **认证**：登录后签发 JWT（HS256，默认有效期 7 天），接口通过 Authorization 头校验身份。
- **限流**：写入与上传接口有独立的频率限制，防止脚本刷爆向量化开销。
- **审计日志**：记录"谁在什么时候做了什么"这类元数据，正文内容不会写入审计日志。
- **备份**：SQLite 场景下每次启动前自动备份数据库，默认保留最近 5 份，出问题可以快速回滚。

## 十、常见问题 FAQ

**Q1：为什么检索不到我刚上传的资料？**
A：先确认文件解析出了文本。扫描版 PDF 抽不出文字需要先做 OCR；再确认片段是否通过了 0.18 的最低相关性阈值，可以换用更贴近原文的提问方式。

**Q2：公共示例库可以删除吗？**
A：可以移入回收站并在回收站恢复；彻底删除只有站长可以执行。

**Q3：为什么一篇文档只出现两段？**
A：这是同文档上限 2 条的设计，目的是让多篇文档都能进入上下文，而不是被一篇长文档占满。

**Q4：内置向量和外部向量服务有什么区别？**
A：内置实现是关键词哈希，只反映字面重合度，优点是零依赖、可离线；配置兼容 OpenAI 的向量服务后会升级为真正的语义向量，此时 0.18 阈值需要重新标定。

**Q5：元信息问题为什么不联网？**
A：作者、页数、上传时间属于你自己的文档，互联网上没有；联网只会返回无关词条，因此这类问题只走知识库。

**Q6：对话性问候（你是谁）会检索知识库吗？**
A：会先检索，但使用更高的相关性门槛（默认 0.30）；如果没有片段达标，就不纳入任何知识库片段，直接给出角色自我介绍，避免把只共享几个常见字的无关片段当成资料。

## 十一、可验证事实点清单

下面每一条都是唯一、可对照的事实，适合用来自测检索是否命中：

- 检索最低相关性阈值：0.18
- 对话性问题相关性门槛：0.30
- 融合算法：RRF（Reciprocal Rank Fusion）
- RRF 常数：60
- 检索通道数：2（稠密向量 + BM25 稀疏）
- BM25 参数：k1=1.5、b=0.75
- 同文档切片上限：2 条
- 支持的文件格式数：40 种
- 切片大小与重叠：600 字 / 100 字
- 检索返回条数：前 4 条
- 上下文字符上限：6000 字
- 长记忆件数：3 件套（滚动摘要 / 历史向量检索 / 自动事实抽取）
- 自动事实抽取频率：每 6 条消息一次
- 联网检索来源数：5 个
- 联网单源超时：8 秒
- 网页结果最低相关性：0.12
- 圆桌 Agent 步骤：4 步（拆解 / 查找 / 审查 / 整理）
- 圆桌审查最大轮次：2 轮
- 默认模型：deepseek-v4-flash
- 向量维度（内置）：512
- 单文件上传上限：20 MB
- 登录令牌算法：HS256
- 备份保留份数：5
- 自动化测试套件（基线）：19 个
- 自动化测试套件（含 self_intro_test）：20 个

## 十二、5 道检索测试题（含标准答案）

**问题 1：检索最低相关性阈值是多少？**
答案：0.18。

**问题 2：两路检索结果用什么算法融合？**
答案：RRF（Reciprocal Rank Fusion，k=60）。

**问题 3：同一文档最多保留几条切片？**
答案：2 条。

**问题 4：系统支持多少种文件格式，上传有哪些安全校验？**
答案：40 种；上传会做扩展名白名单、文件内容签名校验与文件名净化，单文件默认不超过 20 MB。

**问题 5：长记忆三件套是哪三件？**
答案：滚动摘要、历史向量检索、自动事实抽取。

## 十三、干扰段落（用于验证阈值与 Rerank）

这一段与上面的任何系统事实都无关，用来验证相关性阈值与精排能否把它挡在上下文之外：据某地气象台发布，未来三天以多云为主，午后有分散性阵雨，气温 18 至 26 摄氏度；某公司第二季度财报显示营业收入同比增长 7.3%，主要来自订阅制业务，管理层预计下半年毛利率保持稳定。天气与财报信息都与 AI 世界的系统参数无关，若它出现在回答依据里，说明检索相关性控制需要复核。
"""


def public_doc_fingerprint() -> str:
    """当前手册的版本指纹（版本号 + 正文哈希）。

    为什么用哈希而不是"逐字比对"：正文很长，直接比较成本高；哈希还能覆盖
    "只改了标点/个别字"的情况，保证线上一定拿到与代码一致的最新正文。
    """
    payload = str(PUBLIC_DOC_VERSION) + "\n" + PUBLIC_DOC_CONTENT
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_public_doc_content() -> str:
    """在正文前注入版本指纹注释后再入库。

    指纹写进第一段，必然落在 chunk 0（切片按段落打包、第一段很短），
    因此 ensure_public_demo() 只要看任意一条切片是否含当前指纹，就能判断版本。
    """
    header = (
        "<!-- 公共示例手册 · 版本 v" + str(PUBLIC_DOC_VERSION)
        + " · 指纹 " + public_doc_fingerprint() + " -->"
    )
    return header + "\n" + PUBLIC_DOC_CONTENT


async def _resolve_owner(session: AsyncSession) -> User:
    """确定公共库的归属账号：站长账号 > demo 账号 > 第一个用户 > 新建 demo。

    为什么按这个顺序：公共库归属站长最合理（站长才有彻底删除权限），
    但全新库 / 未配置 ADMIN_USERNAMES 时必须能稳妥兜底，绝不能让种子数据失败。
    """
    users = UserRepository(session)
    for name in settings.admin_username_list:
        user = await users.get_by_username(name)
        if user is not None:
            return user
    user = await users.get_by_username(settings.demo_username)
    if user is not None:
        return user
    existing = list(await users.list_all())
    if existing:
        return existing[0]
    created = await users.create(
        username=settings.demo_username,
        password_hash=hash_password(settings.demo_password),
        email="demo@ai-world.local",
        role="admin" if settings.demo_username in settings.admin_username_list else "user",
    )
    await session.commit()
    return created


async def ensure_public_demo(
    session: AsyncSession, embedding_config: EmbeddingConfig | None = None
) -> dict:
    """幂等创建 / 版本化更新「公共示例库 + 使用手册」。

    返回字段：
    - created_knowledge / created_document：本次是否新建（与旧接口兼容）；
    - replaced_document：是否因版本指纹不一致而"删除旧切片 -> 重写新切片"；
    - skipped_reason：knowledge_recycled / document_recycled，说明为何没有动作；
    - chunk_count：当前活着的切片数；document_version：当前版本号。
    """
    knowledge = KnowledgeRepository(session)
    documents = DocumentRepository(session)

    base = await knowledge.get_public_by_name(PUBLIC_KB_NAME)
    created_knowledge = False
    if base is None:
        owner = await _resolve_owner(session)
        base = await knowledge.create(
            user_id=owner.id,
            name=PUBLIC_KB_NAME,
            description=PUBLIC_KB_DESCRIPTION,
            is_public=True,
        )
        await session.commit()
        created_knowledge = True
        logger.info("公共示例库已创建: #%s (owner=%s)", base.id, owner.id)
    else:
        owner = await UserRepository(session).get(base.user_id)
        if owner is None:
            owner = await _resolve_owner(session)

    result = {
        "knowledge_id": base.id,
        "owner_id": owner.id,
        "created_knowledge": created_knowledge,
        "created_document": False,
        "replaced_document": False,
        "skipped_reason": None,
        "chunk_count": 0,
        "document_version": PUBLIC_DOC_VERSION,
    }

    # 知识库已在回收站：不在这里"复活"它（管理员应从回收站恢复），直接跳过。
    if base.deleted_at is not None:
        result["skipped_reason"] = "knowledge_recycled"
        return result

    fingerprint = public_doc_fingerprint()
    existing = await documents.list_by_file(
        base.id, PUBLIC_DOC_STORED_FILENAME, include_deleted=True
    )
    active = [chunk for chunk in existing if chunk.deleted_at is None]

    # 同名文档存在、但切片全在回收站：跳过，不自动复活被有意删除的内容。
    # 恢复后若指纹过旧，下一次调用会走下面的"替换"分支重建为最新版本。
    if existing and not active:
        result["skipped_reason"] = "document_recycled"
        return result

    # 指纹一致：幂等跳过，不重复插入、不重复切片。
    if active and any(fingerprint in (chunk.content or "") for chunk in active):
        result["chunk_count"] = len(active)
        return result

    # 两种情况会走到这里：① 首次入库；② 已有旧内容 -> 先物理删除旧切片再重建。
    if existing:
        await documents.delete_by_file(base.id, PUBLIC_DOC_STORED_FILENAME)
        await session.commit()
        result["replaced_document"] = True
        logger.info("公共示例文档版本过旧，已删除旧切片并准备重建: knowledge=%s", base.id)

    await KnowledgeService(session, embedding_config=embedding_config).upload(
        owner.id,
        filename=PUBLIC_DOC_FILENAME,
        data=build_public_doc_content().encode("utf-8"),
        knowledge_id=base.id,
    )
    result["created_document"] = True
    result["chunk_count"] = await documents.count_by_knowledge(base.id)
    logger.info("公共示例文档已入库: %s (knowledge=%s)", PUBLIC_DOC_FILENAME, base.id)
    return result
