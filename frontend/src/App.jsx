import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout.jsx'
import { useAuth } from './context/AuthContext.jsx'
import Characters from './pages/Characters.jsx'
import Chat from './pages/Chat.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Knowledge from './pages/Knowledge.jsx'
import Login from './pages/Login.jsx'
import Roundtable from './pages/Roundtable.jsx'
import { Spinner } from './components/ui.jsx'

function RequireAuth({ children }) {
  const { token, loading } = useAuth()
  if (!token) return <Navigate to="/login" replace />
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="正在加载你的 AI 世界..." />
      </div>
    )
  }
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="/characters" element={<Characters />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/roundtable" element={<Roundtable />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
