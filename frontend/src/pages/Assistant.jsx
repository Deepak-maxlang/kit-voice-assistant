import VoiceAssistant from '../components/VoiceAssistant.jsx'

export default function Assistant({ user }) {
  const first = user.full_name?.replace(/^(Dr\.|Prof\.)\s*/, '').split(' ')[0]
  return (
    <div className="container section">
      <div className="welcome">
        <div>
          <h1>Hi {first}! 👋</h1>
          <p className="muted">
            <span className={`role-badge ${user.role}`}>{user.role}</span>{' '}
            {[user.class_code, user.department, user.year_of_study ? `Year ${user.year_of_study}` : null].filter(Boolean).join(' · ')}
          </p>
        </div>
        <p className="muted small">Answers use only your own records. Any change (leave, on-duty, approvals, registrations) is confirmed with you first.</p>
      </div>
      <VoiceAssistant mode={user.role} />
    </div>
  )
}
