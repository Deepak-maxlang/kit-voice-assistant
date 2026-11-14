import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

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

  return (
    <div className="page narrow">
      <h1>Student / Teacher login</h1>
      <form onSubmit={submit} className="form">
        <label>Login ID<input value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" required /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
        {error && <p className="error">{error}</p>}
        <button disabled={busy}>{busy ? 'Signing in…' : 'Log in'}</button>
      </form>
    </div>
  )
}
