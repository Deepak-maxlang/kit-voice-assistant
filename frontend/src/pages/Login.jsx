import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

const STUDENTS = ['arjun', 'priya', 'kavya', 'rahul', 'karthik', 'meena', 'divya', 'sneha']
const TEACHERS = ['lakshmi', 'senthil']

export default function Login({ onLogin }) {
  const [loginId, setLoginId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { user } = await api.login(loginId, password)
      onLogin(user)
      navigate('/assistant')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const pick = (id) => { setLoginId(id); setPassword('Kit@2026') }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <span className="logo big">K</span>
        <h1>Sign in to KIT</h1>
        <p className="muted">Students and teachers get personal services: attendance, leave, on-duty and approvals.</p>
        <form onSubmit={submit} className="form">
          <label>Login ID<input value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" required /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn-primary full" disabled={busy}>{busy ? 'Signing in…' : 'Log in'}</button>
        </form>
        <div className="demo">
          <p><b>Demo accounts</b> (password <code>Kit@2026</code>). Click one to fill it in:</p>
          <div className="chips">
            {STUDENTS.map((s) => <button type="button" key={s} className="chip" onClick={() => pick(s)}>{s}</button>)}
            {TEACHERS.map((t) => <button type="button" key={t} className="chip chip-teacher" onClick={() => pick(t)}>{t} · teacher</button>)}
          </div>
        </div>
      </div>
    </div>
  )
}
