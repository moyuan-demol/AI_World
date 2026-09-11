import { useEffect, useState } from 'react'

import { extractError } from '../api/client.js'
import { characterApi } from '../api/endpoints.js'
import Modal from '../components/Modal.jsx'
import { Alert, Button, Card, EmptyState, Field, Input, SectionTitle, Spinner, Textarea } from '../components/ui.jsx'

const PRESETS = [
  {
    label: '医学专家',
    value: {
      name: '张医生',
      role: '医学专家',
      personality: '严谨、耐心、循证',
      expertise: '医疗 AI、临床决策支持、医疗合规',
      speaking_style: '专业、克制，先给结论再给依据',
      system_prompt: '涉及诊断与用药时必须提示不能替代执业医师。',
    },
  },
  {
    label: '技术架构师',
    value: {
      name: '李工',
      role: '技术架构师',
      personality: '直接、结构化',
      expertise: '系统架构、LLM 应用、性能与成本',
      speaking_style: '先给方案对比，再给推荐方案',
      system_prompt: '优先考虑可落地性与成本，明确技术风险。',
    },
  },
  {
    label: '商业顾问',
    value: {
      name: '王顾问',
      role: '商业顾问',
      personality: '数据驱动、关注 ROI',
      expertise: '市场规模、竞品分析、商业模式',
      speaking_style: '量化表达，分层结论',
      system_prompt: '没有真实数据时说明假设，不要编造数字。',
    },
  },
]

const EMPTY_FORM = {
  name: '',
  role: '',
  personality: '',
  expertise: '',
  speaking_style: '',
  system_prompt: '',
}

export default function Characters() {
  const [characters, setCharacters] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const response = await characterApi.list()
      setCharacters(response.data)
      setError('')
    } catch (err) {
      setError(extractError(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const update = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const submit = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await characterApi.create(form)
      setOpen(false)
      setForm(EMPTY_FORM)
      setNotice('AI 伙伴创建成功')
      await load()
    } catch (err) {
      setError(extractError(err))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (character) => {
    if (!window.confirm('确定删除「' + character.name + '」吗？')) return
    try {
      await characterApi.remove(character.id)
      setNotice('已删除「' + character.name + '」')
      await load()
    } catch (err) {
      setError(extractError(err))
    }
  }

  return (
    <div className="space-y-5">
      <SectionTitle
        title="AI 伙伴"
        description="为每个角色定义身份、人格、专长与说话方式，作为对话与圆桌的 System Prompt。"
        action={<Button onClick={() => setOpen(true)}>+ 新建 AI 伙伴</Button>}
      />

      {error ? <Alert tone="error">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      {loading ? (
        <Spinner label="加载中..." />
      ) : characters.length === 0 ? (
        <EmptyState
          icon="🤖"
          title="还没有 AI 伙伴"
          description="创建一个角色，例如「张医生 · 医学专家」，即可在 AI 聊天与 AI 圆桌中使用。"
          action={<Button onClick={() => setOpen(true)}>创建第一个 AI 伙伴</Button>}
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {characters.map((character) => (
            <Card key={character.id} className="flex h-full flex-col p-5">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-brand-50 text-lg">
                    {character.name.slice(0, 1)}
                  </div>
                  <div>
                    <div className="text-base font-semibold text-slate-900">{character.name}</div>
                    <div className="text-xs text-slate-500">{character.role || 'AI 伙伴'}</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => remove(character)}
                  className="rounded-lg px-2 py-1 text-xs text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                >
                  删除
                </button>
              </div>

              <dl className="mt-4 space-y-2 text-sm">
                <div>
                  <dt className="text-xs text-slate-400">人格</dt>
                  <dd className="text-slate-700">{character.personality || '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-400">擅长领域</dt>
                  <dd className="text-slate-700">{character.expertise || '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-400">说话方式</dt>
                  <dd className="text-slate-700">{character.speaking_style || '—'}</dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={open}
        title="新建 AI 伙伴"
        description="可以从预设开始，也可以完全自定义。"
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={submit} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </Button>
          </>
        }
      >
        <form className="space-y-4" onSubmit={submit}>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setForm(preset.value)}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 transition hover:border-brand-300 hover:text-brand-700"
              >
                预设：{preset.label}
              </button>
            ))}
          </div>

          <Field label="名称">
            <Input value={form.name} onChange={update('name')} placeholder="例如：张医生" required />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="身份 / 职业">
              <Input value={form.role} onChange={update('role')} placeholder="例如：医学专家" />
            </Field>
            <Field label="说话方式">
              <Input value={form.speaking_style} onChange={update('speaking_style')} placeholder="例如：专业、简洁" />
            </Field>
          </div>
          <Field label="人格特征">
            <Input value={form.personality} onChange={update('personality')} placeholder="例如：严谨、耐心" />
          </Field>
          <Field label="擅长领域">
            <Textarea value={form.expertise} onChange={update('expertise')} rows={2} placeholder="例如：医疗 AI、临床决策支持" />
          </Field>
          <Field label="补充 System Prompt" hint="用于约束回答风格与边界，例如合规提醒。">
            <Textarea value={form.system_prompt} onChange={update('system_prompt')} rows={3} />
          </Field>
        </form>
      </Modal>
    </div>
  )
}
