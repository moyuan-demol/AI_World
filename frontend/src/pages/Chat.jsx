import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { extractError } from '../api/client.js'
import { characterApi, chatApi, knowledgeApi, systemApi } from '../api/endpoints.js'
import MarkdownText from '../components/MarkdownText.jsx'
import { Alert, Badge, Button, Card, EmptyState, Field, Select, Spinner, Textarea } from '../components/ui.jsx'

function toUiMessage(message) {
  return {
    id: 'stored-' + message.id,
    role: message.role,
    content: message.content,
    sources: [],
    offline: false,
  }
}

export default function Chat() {
  const [characters, setCharacters] = useState([])
  const [bases, setBases] = useState([])
  const [meta, setMeta] = useState(null)

  const [characterId, setCharacterId] = useState('')
  const [knowledgeId, setKnowledgeId] = useState('')
  const [useKnowledge, setUseKnowledge] = useState(true)

  const [messages, setMessages] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const bottomRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [charactersResponse, basesResponse, metaResponse] = await Promise.all([
          characterApi.list(),
          knowledgeApi.list(),
          systemApi.meta(),
        ])
        if (cancelled) return
        setCharacters(charactersResponse.data)
        setBases(basesResponse.data)
        setMeta(metaResponse.data)
        if (charactersResponse.data.length > 0) {
          setCharacterId(String(charactersResponse.data[0].id))
        }
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

  useEffect(() => {
    if (!characterId) return
    let cancelled = false
    const loadHistory = async () => {
      try {
        const [historyResponse, conversationsResponse] = await Promise.all([
          chatApi.history({ character_id: Number(characterId), limit: 100 }),
          chatApi.conversations({ character_id: Number(characterId) }),
        ])
        if (cancelled) return
        setMessages(historyResponse.data.map(toUiMessage))
        const latest = conversationsResponse.data[0]
        setConversationId(latest ? latest.id : null)
      } catch (err) {
        if (!cancelled) setError(extractError(err))
      }
    }
    loadHistory()
    return () => {
      cancelled = true
    }
  }, [characterId])

  useEffect(() => {
    if (bottomRef.current && bottomRef.current.scrollIntoView) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [messages, sending])

  const activeCharacter = characters.find((item) => String(item.id) === String(characterId))

  const send = async () => {
    const text = input.trim()
    if (!text || !characterId || sending) return
    setError('')
    setInput('')
    setMessages((previous) => previous.concat([{ id: 'local-' + Date.now(), role: 'user', content: text }]))
    setSending(true)
    try {
      const payload = {
        character_id: Number(characterId),
        message: text,
        use_knowledge: useKnowledge,
      }
      if (conversationId) payload.conversation_id = conversationId
      if (knowledgeId) payload.knowledge_id = Number(knowledgeId)
      const response = await chatApi.send(payload)
      setConversationId(response.data.conversation_id)
      setMessages((previous) =>
        previous.concat([
          {
            id: 'answer-' + Date.now(),
            role: 'assistant',
            content: response.data.answer,
            sources: response.data.sources || [],
            offline: response.data.offline,
            model: response.data.model,
          },
        ]),
      )
    } catch (err) {
      setError(extractError(err))
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      send()
    }
  }

  const startNew = () => {
    setConversationId(null)
    setMessages([])
  }

  if (loading) {
    return (
      <div className="py-10">
        <Spinner label="加载中..." />
      </div>
    )
  }

  if (characters.length === 0) {
    return (
      <EmptyState
        icon="🤖"
        title="先创建一个 AI 伙伴"
        description="AI 聊天需要一个角色来定义回答的身份与风格。"
        action={
          <Link to="/characters">
            <Button>去创建 AI 伙伴</Button>
          </Link>
        }
      />
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">AI 聊天</h1>
          <p className="mt-1 text-sm text-slate-500">
            提问后系统会先检索你的知识库，再把相关片段与问题一起交给模型。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {meta ? (
            <Badge tone={meta.ai_configured ? 'green' : 'amber'}>
              {meta.ai_configured ? meta.ai_model : '离线演示模式'}
            </Badge>
          ) : null}
          <Button variant="secondary" size="sm" onClick={startNew}>
            + 新对话
          </Button>
        </div>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Card className="h-fit p-5">
          <div className="space-y-4">
            <Field label="选择 AI 伙伴">
              <Select value={characterId} onChange={(event) => setCharacterId(event.target.value)}>
                {characters.map((character) => (
                  <option key={character.id} value={character.id}>
                    {character.name}
                    {character.role ? ' · ' + character.role : ''}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="知识库（RAG 检索范围）">
              <Select
                value={knowledgeId}
                onChange={(event) => setKnowledgeId(event.target.value)}
                disabled={!useKnowledge}
              >
                <option value="">全部知识库</option>
                {bases.map((base) => (
                  <option key={base.id} value={base.id}>
                    {base.name}
                  </option>
                ))}
              </Select>
            </Field>

            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={useKnowledge}
                onChange={(event) => setUseKnowledge(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              启用知识库检索
            </label>

            {activeCharacter ? (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
                <div className="text-sm font-medium text-slate-700">{activeCharacter.name}</div>
                <div className="mt-1">{activeCharacter.role || 'AI 伙伴'}</div>
                {activeCharacter.expertise ? <div className="mt-1">擅长：{activeCharacter.expertise}</div> : null}
                {activeCharacter.personality ? <div className="mt-1">人格：{activeCharacter.personality}</div> : null}
              </div>
            ) : null}

            <div className="text-xs text-slate-400">
              提示：Ctrl / Cmd + Enter 发送；对话记录保存在服务端，按用户隔离。
            </div>
          </div>
        </Card>

        <Card className="flex h-[68vh] flex-col overflow-hidden">
          <div className="flex-1 space-y-4 overflow-y-auto p-5 scrollbar-thin chat-scroll">
            {messages.length === 0 && !sending ? (
              <div className="flex h-full items-center justify-center">
                <div className="text-center">
                  <div className="text-3xl">💬</div>
                  <p className="mt-2 text-sm text-slate-500">
                    向 {activeCharacter ? activeCharacter.name : 'AI 伙伴'} 提出你的第一个问题
                  </p>
                  <div className="mt-4 flex flex-wrap justify-center gap-2">
                    {['帮我梳理一下这个领域的核心要点', '根据知识库总结主要结论', '有哪些风险需要提前注意？'].map((sample) => (
                      <button
                        key={sample}
                        type="button"
                        onClick={() => setInput(sample)}
                        className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-brand-300 hover:text-brand-700"
                      >
                        {sample}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}

            {messages.map((message) =>
              message.role === 'user' ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-3 text-sm text-white shadow-sm">
                    <p className="whitespace-pre-wrap">{message.content}</p>
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm">
                    {activeCharacter ? activeCharacter.name.slice(0, 1) : 'AI'}
                  </div>
                  <div className="max-w-[85%] rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 shadow-sm">
                    <MarkdownText text={message.content} />
                    {message.sources && message.sources.length > 0 ? (
                      <div className="mt-3 border-t border-slate-100 pt-2">
                        <div className="text-xs text-slate-400">引用来源</div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {message.sources.map((source) => (
                            <span
                              key={source.document_id}
                              className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                              title={source.snippet}
                            >
                              {source.filename} · {source.score}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {message.offline ? (
                      <div className="mt-2 text-xs text-amber-600">离线演示回答（未配置 DEEPSEEK_API_KEY）</div>
                    ) : null}
                  </div>
                </div>
              ),
            )}

            {sending ? (
              <div className="flex items-center gap-3 text-sm text-slate-500">
                <Spinner label="正在检索知识库并生成回答..." />
              </div>
            ) : null}

            <div ref={bottomRef} />
          </div>

          <div className="border-t border-slate-100 p-4">
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              rows={3}
              placeholder="输入你的问题，Ctrl / Cmd + Enter 发送"
            />
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-slate-400">
                {useKnowledge ? '已启用知识库检索' : '仅使用角色设定回答'}
              </span>
              <Button onClick={send} disabled={sending || !input.trim()}>
                {sending ? '生成中...' : '发送'}
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
