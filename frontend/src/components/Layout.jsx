import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import Sidebar from './Sidebar.jsx'

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex h-full w-full overflow-hidden bg-slate-100">
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      {mobileOpen ? (
        <div className="fixed inset-0 z-30 flex md:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMobileOpen(false)} />
          <div className="relative z-10">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 md:hidden">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600"
          >
            菜单
          </button>
          <span className="text-sm font-semibold text-slate-800">AI World</span>
          <span className="w-12" />
        </header>

        <main className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
