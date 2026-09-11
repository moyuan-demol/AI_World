import { useEffect, useRef, useState } from 'react'

import { extractError } from '../api/client.js'
import { knowledgeApi, systemApi } from '../api/endpoints.js'
import Modal from '../components/Modal.jsx'
import { Alert, Badge, Button, Card, EmptyState, Field, Input, SectionTitle, Select, Spinner, Textarea } from '../components/ui.jsx'

export default function Knowledge() {
  const fileInputRef = useRef(null)

  const [bases, setBases] = useState([])
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [targetKb, setTargetKb] = useState('')
  const [uploading, setUploading] = useState(false)

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ name: '', description: '' })
  const [saving, setSaving] = useState(false)

  const [activeKb, setActiveKb] = useState(null)
  const [documents, setDocuments] = useState([])
  const [documentsLoading, setDocumentsLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [basesResponse, metaResponse] = await Promise.all([knowledgeApi.list(), systemApi.meta()])
      setBases(basesResponse.data)
      setMeta(metaResponse.data)
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

  const handleCreate = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await knowledgeApi.create(form)
      setCreateOpen(false)
      setForm({ name: '', description: '' })
      setNotice('知识库创建成功')
      await load()
    } catch (err) {
      setError(extractError(err))
    } finally {
      setSaving(false)
    }
  }

  const handleUpload = async (event) => {
    const file = event.target.files && event.target.files[0]
    if (!file) return
    setUploading(true)
    setError('')
    setNotice('')
    try {
      const options = targetKb ? { knowledgeId: Number(targetKb) } : { name: file.name }
      const response = await knowledgeApi.upload(file, options)
      setNotice(
        '已入库：' +
          response.data.filename +
          '，生成 ' +
          response.data.chunk_count +
          ' 个向量切片（' +
          response.data.char_count +
          ' 字符）',
      )
      await load()
    } catch (err) {
      setError(extractError(err))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const openDocuments = async (base) => {
    setActiveKb(base)
    setDocuments([])
    setDocumentsLoading(true)
    try {
      const response = await knowledgeApi.documents(base.id, 100)
      setDocuments(response.data)
    } catch (err) {
      setError(extractError(err))
    } finally {
      setDocumentsLoading(false)
    }
  }

  const remove = async (base) => {
    if (!window.confirm('确定删除知识库「' + base.name + '」及其全部切片吗？')) return
    try {
      await knowledgeApi.remove(base.id)
      setNotice('已删除知识库「' + base.name + '」')
      await load()
    } catch (err) {
      setError(extractError(err))
    }
  }

  const acceptList = meta && meta.allowed_extensions ? meta.allowed_extensions.join(',') : '.pdf,.docx,.txt,.md'

  return (
    <div className="space-y-5">
      <SectionTitle
        title="知识世界"
        description="上传资料后系统会自动解析、切片、向量化，AI 回答时按相似度检索并引用。"
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setCreateOpen(true)}>
              新建知识库
            </Button>
            <Button onClick={() => fileInputRef.current && fileInputRef.current.click()} disabled={uploading}>
              {uploading ? '上传解析中...' : '上传文件'}
            </Button>
          </div>
        }
      />

      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept={acceptList}
        onChange={handleUpload}
      />

      {error ? <Alert tone="error">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      <Card className="p-5">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="上传到" hint="不选择则按文件名自动创建知识库">
            <Select value={targetKb} onChange={(event) => setTargetKb(event.target.value)}>
              <option value="">自动创建新知识库</option>
              {bases.map((base) => (
                <option key={base.id} value={base.id}>
                  {base.name}
                </option>
              ))}
            </Select>
          </Field>
          <div className="md:col-span-2 flex flex-col justify-end gap-2 text-xs text-slate-500">
            <div>
              允许格式：{(meta && meta.allowed_extensions ? meta.allowed_extensions.join(' / ') : '.pdf / .docx / .txt / .md')}
            </div>
            <div>单文件上限：{meta ? meta.max_upload_mb : 20} MB · 服务端会做扩展名与文件签名双重校验</div>
            <div>
              向量模型：{meta ? meta.embedding_provider : 'local'}（{meta ? meta.embedding_dim : 512} 维）
            </div>
          </div>
        </div>
      </Card>

      {loading ? (
        <Spinner label="加载中..." />
      ) : bases.length === 0 ? (
        <EmptyState
          icon="📚"
          title="还没有知识库"
          description="上传一个 PDF、DOCX、TXT 或 Markdown 文件，AI World 会自动完成解析与向量化。"
          action={<Button onClick={() => fileInputRef.current && fileInputRef.current.click()}>上传第一个文件</Button>}
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {bases.map((base) => (
            <Card key={base.id} className="flex h-full flex-col p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-base font-semibold text-slate-900">{base.name}</div>
                  <div className="mt-1">
                    <Badge tone="brand">{base.document_count} 个切片</Badge>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => remove(base)}
                  className="rounded-lg px-2 py-1 text-xs text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                >
                  删除
                </button>
              </div>
              <p className="mt-3 line-clamp-3 flex-1 text-sm text-slate-500">{base.description || '暂无描述'}</p>
              <div className="mt-4 flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => openDocuments(base)}>
                  查看切片
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setTargetKb(String(base.id))
                    if (fileInputRef.current) fileInputRef.current.click()
                  }}
                >
                  追加文件
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        title="新建知识库"
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button onClick={handleCreate} disabled={saving}>
              {saving ? '创建中...' : '创建'}
            </Button>
          </>
        }
      >
        <form className="space-y-4" onSubmit={handleCreate}>
          <Field label="名称">
            <Input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：医疗 AI 行业资料"
              required
            />
          </Field>
          <Field label="描述">
            <Textarea
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="这批资料的主题、来源或用途"
            />
          </Field>
        </form>
      </Modal>

      <Modal
        open={Boolean(activeKb)}
        title={activeKb ? '切片预览 · ' + activeKb.name : ''}
        description="每个切片是一个向量检索单元，命中后会作为上下文拼进 Prompt。"
        onClose={() => setActiveKb(null)}
      >
        {documentsLoading ? (
          <Spinner label="加载切片中..." />
        ) : documents.length === 0 ? (
          <p className="text-sm text-slate-500">该知识库还没有切片。</p>
        ) : (
          <div className="space-y-3">
            {documents.map((document) => (
              <div key={document.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
                  <span>{document.filename}</span>
                  <span>#{document.chunk_index}</span>
                </div>
                <p className="whitespace-pre-wrap text-sm text-slate-700">{document.content}</p>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
