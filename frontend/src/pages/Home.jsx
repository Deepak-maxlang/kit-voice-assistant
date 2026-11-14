import { useEffect, useState } from 'react'
import VoiceAssistant from '../components/VoiceAssistant.jsx'
import { api } from '../api'

const inr = (n) => `₹${Number(n).toLocaleString('en-IN')}`

export default function Home() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { api.college().then(setData).catch((e) => setError(e.message)) }, [])

  const c = data?.college
  return (
    <div className="page">
      <section className="hero">
        <h1>{c?.name || 'Knotopian Institute of Technology'}</h1>
        {c?.tagline && <p className="tagline">{c.tagline}</p>}
        <p>{c?.description || (error ? 'College information is temporarily unavailable.' : 'Loading…')}</p>
        {c?.contact && (
          <p className="muted">{c.contact.address} · {c.contact.phone} · {c.contact.email}</p>
        )}
      </section>

      <section>
        <h2>Courses</h2>
        <div className="grid">
          {(data?.courses || []).map((course) => (
            <div className="card" key={course.code}>
              <h3>{course.name}</h3>
              <p>Code: {course.code}</p>
              <p>Minimum 12th: {course.min_12th_percent}%</p>
              <p>Annual fee: {inr(course.annual_fee)} · Hostel: {inr(course.hostel_fee)}</p>
              <p>Seats: {course.seats}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Talk to us</h2>
        <p className="muted">Ask about courses, fees, eligibility, dates and documents, or apply for admission — by voice.</p>
        <VoiceAssistant mode="public" />
      </section>
    </div>
  )
}
