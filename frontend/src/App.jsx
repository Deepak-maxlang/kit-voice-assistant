import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
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

  if (user === undefined) return <div className="center"><span className="spinner" aria-label="Loading" /></div>

  return (
    <>
      <header className="nav">
        <div className="nav-inner">
          <Link to="/" className="brand" aria-label="KIT home">
            <span className="logo">K</span>
            <span className="brand-text"><b>KIT</b><small>Knotopian Institute of Technology</small></span>
          </Link>
          <nav aria-label="Main">
            <NavLink to="/" end>Home</NavLink>
            {user && <NavLink to="/assistant">Assistant</NavLink>}
            <NavLink to="/history">History</NavLink>
            {user ? (
              <span className="user-chip">
                <span className={`role-badge ${user.role}`}>{user.role}</span>
                <span className="user-name">{user.full_name}</span>
                <button className="btn-ghost" onClick={logout}>Log out</button>
              </span>
            ) : (
              <Link to="/login" className="btn-small">Student / Teacher login</Link>
            )}
          </nav>
        </div>
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
      <footer className="footer">© Knotopian Institute of Technology</footer>
    </>
  )
}
