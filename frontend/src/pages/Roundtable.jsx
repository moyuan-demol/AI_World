import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { extractError } from '../api/client.js'
import { knowledgeApi, roundtableApi, systemApi } from '../api/endpoints.js'
import MarkdownText from '../components/MarkdownText.jsx'
import { Alert, Badge, Button, Card, EmptyState, Field, Select, Spinner, Textarea } from '../components/ui.jsx'

const SAMPLES = [
  '如何开发一款医疗 AI 产品？',
  '我们要不要为大模型应用引入知识库？',
  '面向中小企业的 AI 助手，应该先做哪个功能？',
]

function agentKey(agent) {
  return (agent.source || 'default') + ':' + agent.agent
}

export default function Roundtable() {
  const [agents, setAgents] = useState([])
  const [bases, setBases] = useState([])
  const [meta, setMeta] = useState(null)
  const [selected, setSelected] = useState([])
  const [question, setQuestion] = useState('')
  const [useKnowledge, setUseKnowledge] = useState(false)
  const [knowledgeId, setKnowledgeId] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [agentsResponse, basesResponse, metaResponse] = await Promise.all([
          roundtableApi.agents(),
          knowledgeApi.list(),
          systemApi.meta(),
        ])
        if (cancelled) return
        setAgents(agentsResponse.data)
        setBases(basesResponse.data)
        setMeta(metaResponse.data)
        setSelected(agentsResponse.data.filter((item) => item.source === 'default').map(agentKey))
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

  const toggle = (key) => {
    setSelected((previous) =>
      previous.includes(key) ? previous.filter((item) => item !== key) : previous.concat([key]),
    )
  }

  const run = async () => {
    const text = question.trim()
    if (!text || running) return
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const chosen = agents.filter((agent) => selected.includes(agentKey(agent)))
      const payload = {
        question: text,
        use_knowledge: useKnowledge,
        include_manager: true,
      }
      if (chosen.length > 0) {
        payload.agents = chosen.map((agent) => ({
          agent: agent.agent,
          role: agent.role || '',
          goal: agent.goal || '',
          personality: agent.personality || '',
        }))
      }
      if (knowledgeId) payload.knowledge_id = Number(knowledgeId)
      const response = await roundtableApi.run(payload)
      setResult(response.data)
    } catch (err) {
      setError(extractError(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">AI 圆桌</h1>
          <p className="mt-1 text-sm text-slate-500">
            主持 Agent 拆解议题 → 多个专家 Agent 并行发言 → 主持 Agent 汇总为最终方案。
          </p>
        </div>
        {meta ? (
          <Badge tone={meta.ai_configured ? 'green' : 'amber'}>
            {meta.ai_configured ? 'DeepSeek 已连接' : '离线演示模式'}
          </Badge>
        ) : null}
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}

      {loading ? (
        <Spinner label="加载中..." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <Card className="h-fit p-5">
            <div className="space-y-4">
              <Field label="讨论议题">
                <Textarea
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  rows={4}
                  placeholder="例如：如何开发一款医疗 AI 产品？"
                />
              </Field>

              <div className="flex flex-wrap gap-2">
                {SAMPLES.map((sample) => (
                  <button
                    key={sample}
                    type="button"
                    onClick={() => setQuestion(sample)}
                    className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-brand-300 hover:text-brand-700"
                  >
                    {sample}
                  </button>
                ))}
              </div>

              <div>
                <div className="mb-2 text-sm font-medium text-slate-700">与会 Agent</div>
                <div className="space-y-2">
                  {agents.map((agent) => {
                    const key = agentKey(agent)
                    return (
                      <label
                        key={key}
                        className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 p-3 transition hover:border-brand-200"
                      >
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 rounded border-slate-300"
                          checked={selected.includes(key)}
                          onChange={() => toggle(key)}
                        />
                        <span>
                          <span className="block text-sm font-medium text-slate-800">{agent.agent}</span>
                          <span className="block text-xs text-slate-500">{agent.role || 'AI 专家'}</span>
                          {agent.source === 'character' ? (
                            <span className="mt-1 inline-block text-xs text-brand-600">来自我的 AI 伙伴</span>
                          ) : (
                            <span className="mt-1 inline-block text-xs text-slate-400">内置角色</span>
                          )}
                        </span>
                      </label>
                    )
                  })}
                </div>
                {agents.filter((agent) => agent.source === 'character').length === 0 ? (
                  <p className="mt-2 text-xs text-slate-400">
                    还可以把自己创建的 AI 伙伴拉进圆桌：
                    <Link to="/characters" className="ml-1 text-brand-600 hover:text-brand-700">
                      去创建
                    </Link>
                  </p>
                ) : null}
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300"
                  checked={useKnowledge}
                  onChange={(event) => setUseKnowledge(event.target.checked)}
                />
                让 Agent 参考知识库
              </label>

              {useKnowledge ? (
                <Select value={knowledgeId} onChange={(event) => setKnowledgeId(event.target.value)}>
                  <option value="">全部知识库</option>
                  {bases.map((base) => (
                    <option key={base.id} value={base.id}>
                      {base.name}
                    </option>
                  ))}
                </Select>
              ) : null}

              <Button className="w-full" onClick={run} disabled={running || !question.trim()}>
                {running ? '圆桌讨论中...' : '发起圆桌讨论'}
              </Button>
              <p className="text-xs text-slate-400">
                未勾选任何 Agent 时，服务端会使用内置的产品经理 / 技术专家 / 商业顾问组合。
              </p>
            </div>
          </Card>

          <div className="space-y-4">
            {running ? (
              <Card className="p-8">
                <div className="flex flex-col items-center gap-3">
                  <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-brand-600" />
                  <div className="text-sm text-slate-500">
                    主持 Agent 正在拆解议题，并等待各专家 Agent 并行回复...
                  </div>
                </div>
              </Card>
            ) : null}

            {!running && !result ? (
              <EmptyState
                icon="🪑"
                title="还没有讨论结果"
                description="输入议题并选择与会 Agent，AI 圆桌会给出多视角分析与主持人汇总方案。"
              />
            ) : null}

            {result ? (
              <div className="space-y-4">
                {result.manager_brief ? (
                  <Card className="border-brand-100 bg-brand-50/60 p-5">
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold text-brand-800">主持人拆解</div>
                      {result.offline ? <Badge tone="amber">离线演示</Badge> : null}
                    </div>
                    <MarkdownText text={result.manager_brief} className="mt-2" />
                  </Card>
                ) : null}

                <div className="grid gap-4 md:grid-cols-2">
                  {result.results.map((item) => (
                    <Card key={item.agent + '-' + item.role} className="p-5">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-sm font-semibold text-slate-900">{item.agent}</div>
                          <div className="text-xs text-slate-500">{item.role}</div>
                        </div>
                        <Badge tone={item.offline ? 'amber' : 'brand'}>{item.offline ? '离线' : item.model}</Badge>
                      </div>
                      <div className="mt-3">
                        <MarkdownText text={item.answer} />
                      </div>
                    </Card>
                  ))}
                </div>

                {result.summary ? (
                  <Card className="border-emerald-100 p-5">
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold text-emerald-800">
                        {result.manager || '主持 Agent'} · 最终汇总
                      </div>
                      {result.sources && result.sources.length > 0 ? (
                        <span className="text-xs text-slate-500">参考：{result.sources.join('、')}</span>
                      ) : null}
                    </div>
                    <div className="mt-3">
                      <MarkdownText text={result.summary} />
                    </div>
                  </Card>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}
