import { useEffect, useState } from 'react'
import { api } from '../api'

const fmt = (ts) => new Date(ts * 1000).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })

export default function History({ user }) {
  const [list, setList] = useState(null)
  const [active, setActive] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { api.conversations().then((d) => setList(d.conversations)).catch((e) => setError(e.message)) }, [user])

  const open = (id) => api.conversation(id).then(setActive).catch((e) => setError(e.message))

  return (
    <div className="container section">
      <h1>Conversation history</h1>
      <p className="muted">{user ? `Your past conversations, ${user.full_name}.` : 'Conversations from this browser. Log in to see your account history.'}</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="history">
        <ul className="conv-list" aria-label="Conversations">
          {list && list.length === 0 && <li className="empty">No conversations yet.</li>}
          {(list || []).map((c) => (
            <li key={c.id}>
              <button className={`conv-item ${active?.conversation?.id === c.id ? 'active' : ''}`} onClick={() => open(c.id)}>
                <span className="conv-title">{c.title || 'Conversation'}</span>
                <span className="conv-meta">{fmt(c.updated_at)} · {c.message_count} messages · <span className={`role-badge ${c.mode}`}>{c.mode}</span></span>
              </button>
            </li>
          ))}
        </ul>
        <div className="conv-view panel">
          {!active && <p className="muted">Select a conversation to read it.</p>}
          {active && <p className="muted small">Started {fmt(active.conversation.created_at)}</p>}
          {active?.messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              <span>{m.content}</span>
              {m.meta?.actions?.length > 0 && (
                <small className="actions">{m.meta.actions.map((a) => `${a.tool.replaceAll('_', ' ')}: ${a.status.replaceAll('_', ' ')}`).join(' · ')}</small>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
