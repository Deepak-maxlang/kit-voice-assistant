import { useEffect, useState } from 'react'
import { api } from '../api'

const fmt = (ts) => new Date(ts * 1000).toLocaleString()

export default function History({ user }) {
  const [list, setList] = useState([])
  const [active, setActive] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { api.conversations().then((d) => setList(d.conversations)).catch((e) => setError(e.message)) }, [user])

  const open = (id) => api.conversation(id).then(setActive).catch((e) => setError(e.message))

  return (
    <div className="page">
      <h1>Conversation history</h1>
      <p className="muted">{user ? `Your past conversations, ${user.full_name}.` : 'Conversations from this browser (log in to see your account history).'}</p>
      {error && <p className="error">{error}</p>}
      <div className="history">
        <ul className="conv-list">
          {list.length === 0 && <li className="muted">No conversations yet.</li>}
          {list.map((c) => (
            <li key={c.id} className={active?.conversation?.id === c.id ? 'active' : ''}>
              <button className="link" onClick={() => open(c.id)}>
                <b>{c.title || 'Conversation'}</b><br />
                <small>{fmt(c.updated_at)} · {c.message_count} messages · {c.mode}</small>
              </button>
            </li>
          ))}
        </ul>
        <div className="conv-view">
          {!active && <p className="muted">Select a conversation.</p>}
          {active?.messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              <span>{m.content}</span>
              {m.meta?.actions?.length > 0 && (
                <small className="actions">{m.meta.actions.map((a) => `${a.tool}: ${a.status}`).join(' · ')}</small>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
