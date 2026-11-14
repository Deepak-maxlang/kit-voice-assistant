import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import Home from './pages/Home.jsx'
import Login from './pages/Login.jsx'
import Assistant from './pages/Assistant.jsx'
import History from './pages/History.jsx'

export default function App() {
  const [user, setUser] = useState(undefined) // undefined = loading
  const navigate = useNavigate()

  useEffect(() => { api.me().then((d) => setUser(d.user)).catch(() => setUser(null)) }, [])

  const logout = async () => {
    await api.logout().catch(() => {})
    setUser(null)
    navigate('/')
  }

  if (user === undefined) return <div className="center">Loading…</div>

  return (
    <>
      <header className="nav">
        <Link to="/" className="brand">KIT</Link>
        <nav>
          <Link to="/">Home</Link>
          {user && <Link to="/assistant">Assistant</Link>}
          <Link to="/history">History</Link>
          {user ? (
            <button className="link" onClick={logout}>Log out ({user.full_name})</button>
          ) : (
            <Link to="/login">Student / Teacher login</Link>
          )}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={user ? <Navigate to="/assistant" /> : <Login onLogin={setUser} />} />
          <Route path="/assistant" element={user ? <Assistant user={user} /> : <Navigate to="/login" />} />
          <Route path="/history" element={<History user={user} />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </>
  )
}
