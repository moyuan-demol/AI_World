import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { extractError } from '../api/client.js'
import { Alert, Button, Field, Input } from '../components/ui.jsx'
import { useAuth } from '../context/AuthContext.jsx'

export default function Login() {
  const { token, login, register, demoLogin } = useAuth()
  const navigate = useNavigate()

  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (token) return <Navigate to="/" replace />

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mode === 'login') {
        await login(username.trim(), password)
      } else {
        await register(username.trim(), password, email.trim())
      }
      navigate('/', { replace: true })
    } catch (err) {
      setError(extractError(err))
    } finally {
      setBusy(false)
    }
  }

  const handleDemo = async () => {
    setError('')
    setBusy(true)
    try {
      await demoLogin()
      navigate('/', { replace: true })
    } catch (err) {
      setError(extractError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-slate-100 p-4">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-soft md:grid-cols-2">
        <div className="relative hidden flex-col justify-between bg-gradient-to-br from-slate-900 via-brand-800 to-brand-600 p-8 text-white md:flex">
          <div>
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/15 text-lg font-bold">
              AI
            </div>
            <h1 className="mt-6 text-3xl font-semibold leading-tight">AI World</h1>
            <p className="mt-2 text-sm text-white/80">AI 世界 · 你的个人 AI 智能空间</p>
          </div>
          <ul className="space-y-3 text-sm text-white/85">
            <li>🤖 创建属于你的 AI 伙伴</li>
            <li>📚 上传资料，构建知识世界</li>
            <li>💬 基于知识库的 RAG 对话</li>
            <li>🪑 多 Agent AI 圆桌协作</li>
          </ul>
          <p className="text-xs text-white/60">所有数据按用户隔离，API Key 只保存在服务端 .env</p>
        </div>

        <div className="p-8">
          <h2 className="text-xl font-semibold text-slate-900">
            {mode === 'login' ? '登录 AI World' : '创建新账号'}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {mode === 'login' ? '输入账号密码，进入你的 AI 世界' : '注册后即可创建自己的 AI 伙伴'}
          </p>

          <form className="mt-6 space-y-4" onSubmit={submit}>
            <Field label="用户名">
              <Input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="至少 2 个字符"
                autoComplete="username"
                required
              />
            </Field>

            {mode === 'register' ? (
              <Field label="邮箱（可选）">
                <Input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                />
              </Field>
            ) : null}

            <Field label="密码" hint={mode === 'register' ? '至少 6 位' : undefined}>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                required
              />
            </Field>

            {error ? <Alert tone="error">{error}</Alert> : null}

            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? '处理中...' : mode === 'login' ? '登录' : '注册并进入'}
            </Button>
          </form>

          <div className="mt-4 flex items-center justify-between text-sm">
            <button
              type="button"
              className="text-brand-600 hover:text-brand-700"
              onClick={() => {
                setMode(mode === 'login' ? 'register' : 'login')
                setError('')
              }}
            >
              {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
            </button>
          </div>

          <div className="mt-6 rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4">
            <div className="text-sm font-medium text-slate-700">想直接看 Demo？</div>
            <p className="mt-1 text-xs text-slate-500">
              使用内置演示账号 demo / demo123，一键体验全部 Phase 1 功能。
            </p>
            <Button variant="dark" className="mt-3 w-full" onClick={handleDemo} disabled={busy}>
              一键体验 Demo
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
