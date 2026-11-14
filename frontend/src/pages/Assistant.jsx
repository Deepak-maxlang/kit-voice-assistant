import VoiceAssistant from '../components/VoiceAssistant.jsx'

const HINTS = {
  student: ['What is my attendance?', 'Can I take leave on 25 November?', 'Apply on-duty for a hackathon on 28 November',
    'Am I eligible for any placement drive?', 'Explain normalisation in DBMS'],
  teacher: ['Show pending requests', 'Show attendance of my class', 'Approve request 12'],
}

export default function Assistant({ user }) {
  return (
    <div className="page">
      <h1>Hi {user.full_name?.split(' ')[0]}!</h1>
      <p className="muted">
        Logged in as <b>{user.role}</b>{user.class_code ? ` · ${user.class_code}` : ''}{user.department ? ` · ${user.department}` : ''}.
        Try: {(HINTS[user.role] || []).map((h) => `“${h}”`).join(', ')}
      </p>
      <VoiceAssistant mode={user.role} />
    </div>
  )
}
