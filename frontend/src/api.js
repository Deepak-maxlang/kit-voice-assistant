// Thin API client. Cookies carry the session; no tokens are ever stored in the browser.
async function request(path, options = {}) {
  const res = await fetch(path, { credentials: 'include', ...options })
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error?.message)) || `Request failed (${res.status})`
    throw new Error(typeof msg === 'string' ? msg : 'Request failed')
  }
  return data
}

const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  me: () => request('/api/me'),
  login: (login_id, password) => request('/api/auth/login', json({ login_id, password })),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  college: () => request('/api/public/college'),
  chat: (message, conversation_id, speak = true) => request('/api/chat', json({ message, conversation_id, speak })),
  voice: (blob, conversation_id) => {
    const form = new FormData()
    form.append('audio', blob, 'speech.webm')
    if (conversation_id) form.append('conversation_id', conversation_id)
    return request('/api/voice', { method: 'POST', body: form })
  },
  conversations: () => request('/api/conversations'),
  conversation: (id) => request(`/api/conversations/${id}`),
}
