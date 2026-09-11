import { NavLink } from 'react-router-dom'

import { useAuth } from '../context/AuthContext.jsx'

const NAV_ITEMS = [
  { to: '/', label: '我的世界', emoji: '🌍', end: true },
  { to: '/characters', label: 'AI伙伴', emoji: '🤖' },
  { to: '/knowledge', label: '知识世界', emoji: '📚' },
  { to: '/chat', label: 'AI聊天', emoji: '💬' },
  { to: '/roundtable', label: 'AI圆桌', emoji: '🪑' },
]

export default function Sidebar({ onNavigate }) {
  const { user, logout } = useAuth()

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col bg-slate-900 text-slate-300">
      <div className="flex items-center gap-3 px-5 py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-400 to-brand-700 text-lg font-bold text-white">
          AI
        </div>
        <div>
          <div className="text-base font-semibold text-white">AI World</div>
          <div className="text-xs text-slate-400">AI 世界 · 智能空间</div>
        </div>
      </div>

      <nav className="mt-2 flex-1 space-y-1 px-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              [
                'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition',
                isActive
                  ? 'bg-brand-600 text-white shadow-sm'
                  : 'text-slate-300 hover:bg-slate-800 hover:text-white',
              ].join(' ')
            }
          >
            <span className="text-base">{item.emoji}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-slate-800 px-4 py-4">
        <div className="mb-3 flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-800 text-sm text-white">
            {(user && user.username ? user.username[0] : 'A').toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm text-white">{user ? user.username : '未登录'}</div>
            <div className="truncate text-xs text-slate-400">{user && user.email ? user.email : '个人空间'}</div>
          </div>
        </div>
        <button
          type="button"
          onClick={logout}
          className="w-full rounded-xl border border-slate-700 px-3 py-2 text-xs text-slate-300 transition hover:bg-slate-800 hover:text-white"
        >
          退出登录
        </button>
      </div>
    </aside>
  )
}
