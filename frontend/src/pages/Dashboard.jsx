import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { extractError } from '../api/client.js'
import { characterApi, knowledgeApi, systemApi } from '../api/endpoints.js'
import { Alert, Badge, Card, Spinner, StatCard } from '../components/ui.jsx'
import { useAuth } from '../context/AuthContext.jsx'

const MODULES = [
  {
    to: '/characters',
    emoji: '🤖',
    title: 'AI 伙伴',
    description: '创建具有身份、人格与专长的 AI 角色，例如张医生、李工、王顾问。',
  },
  {
    to: '/knowledge',
    emoji: '📚',
    title: '知识世界',
    description: '上传 PDF / DOCX / TXT / MD，自动切片并向量化，成为 AI 的知识来源。',
  },
  {
    to: '/chat',
    emoji: '💬',
    title: 'AI 聊天',
    description: '选择 AI 伙伴提问，系统自动检索知识库并拼接 Prompt 后交给 DeepSeek。',
  },
  {
    to: '/roundtable',
    emoji: '🪑',
    title: 'AI 圆桌',
    description: '多个 Agent 分别从产品、技术、商业视角讨论，由主持 Agent 汇总最终方案。',
  },
]

export default function Dashboard() {
  const { user } = useAuth()
  const [characters, setCharacters] = useState([])
  const [knowledge, setKnowledge] = useState([])
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [charactersResponse, knowledgeResponse, metaResponse] = await Promise.all([
          characterApi.list(),
          knowledgeApi.list(),
          systemApi.meta(),
        ])
        if (cancelled) return
        setCharacters(charactersResponse.data)
        setKnowledge(knowledgeResponse.data)
        setMeta(metaResponse.data)
      } catch (err) {
        if (!cancelled) setError(extractError(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  const documentCount = knowledge.reduce((total, item) => total + (item.document_count || 0), 0)

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <div className="bg-gradient-to-r from-slate-900 via-brand-800 to-brand-600 px-6 py-8 text-white">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-sm text-white/70">欢迎进入 AI World</div>
              <h1 className="mt-1 text-3xl font-semibold">
                {(user && user.username ? user.username : '朋友') + '，你的 AI 世界已就绪'}
              </h1>
              <p className="mt-2 max-w-2xl text-sm text-white/80">
                在这里创建你的 AI 伙伴、上传知识、发起对话，或让多个 AI 角色围绕一个议题展开圆桌讨论。
              </p>
            </div>
            <div className="flex flex-col items-start gap-2">
              <Badge tone={meta && meta.ai_configured ? 'green' : 'amber'}>
                {meta && meta.ai_configured ? 'DeepSeek 已连接' : '离线演示模式（未配置 API Key）'}
              </Badge>
              {meta ? (
                <span className="text-xs text-white/70">
                  模型 {meta.ai_model} · 向量 {meta.embedding_provider}/{meta.embedding_dim}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </Card>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {loading ? (
        <div className="py-6">
          <Spinner label="正在加载..." />
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="AI 伙伴" value={characters.length} hint="可随时扩展" icon="🤖" />
        <StatCard label="知识库" value={knowledge.length} hint="支持 4 种文件格式" icon="📚" />
        <StatCard label="向量切片" value={documentCount} hint="自动切片入库" icon="🧩" />
        <StatCard label="运行模式" value={meta && meta.ai_configured ? '在线' : '离线'} hint="DeepSeek API" icon="⚡" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {MODULES.map((item) => (
          <Link key={item.to} to={item.to} className="group">
            <Card className="h-full p-5 transition group-hover:-translate-y-0.5 group-hover:shadow-lg">
              <div className="flex items-start gap-4">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-50 text-xl">
                  {item.emoji}
                </div>
                <div>
                  <div className="text-base font-semibold text-slate-900">{item.title}</div>
                  <p className="mt-1 text-sm text-slate-500">{item.description}</p>
                  <span className="mt-3 inline-block text-sm font-medium text-brand-600">进入 →</span>
                </div>
              </div>
            </Card>
          </Link>
        ))}
      </div>

      <Card className="p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">你的 AI 伙伴</h2>
          <Link to="/characters" className="text-sm text-brand-600 hover:text-brand-700">
            管理全部
          </Link>
        </div>
        {characters.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">还没有 AI 伙伴，去「AI伙伴」页面创建一个吧。</p>
        ) : (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {characters.slice(0, 6).map((character) => (
              <div key={character.id} className="rounded-xl border border-slate-200 p-4">
                <div className="text-sm font-semibold text-slate-900">{character.name}</div>
                <div className="mt-1 text-xs text-slate-500">{character.role || 'AI 伙伴'}</div>
                {character.expertise ? (
                  <div className="mt-2 line-clamp-2 text-xs text-slate-400">{character.expertise}</div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
