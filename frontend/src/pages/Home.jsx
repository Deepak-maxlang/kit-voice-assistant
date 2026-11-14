import { useEffect, useState } from 'react'
import VoiceAssistant from '../components/VoiceAssistant.jsx'
import { api } from '../api'

const inr = (n) => `₹${Number(n).toLocaleString('en-IN')}`

export default function Home() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { api.college().then(setData).catch((e) => setError(e.message)) }, [])

  const c = data?.college
  const totalSeats = (data?.courses || []).reduce((n, x) => n + (x.seats || 0), 0)
  const scrollToAssistant = () => document.getElementById('talk')?.scrollIntoView({ behavior: 'smooth' })

  return (
    <>
      <section className="hero">
        <div className="hero-inner">
          <p className="eyebrow">Admissions open · 2027–28 intake</p>
          <h1>{c?.name || 'Knotopian Institute of Technology'}</h1>
          <p className="tagline">{c?.tagline || 'Excellence in Engineering and Applied Technology'}</p>
          <p className="hero-desc">{c?.description || (error ? 'College information is temporarily unavailable.' : 'Loading…')}</p>
          <div className="hero-actions">
            <button className="btn-primary" onClick={scrollToAssistant}>🎤 Talk to us</button>
            <a className="btn-outline" href="#courses">Explore courses</a>
          </div>
          {c?.contact && (
            <p className="hero-contact">{c.contact.address} · {c.contact.phone} · {c.contact.email}</p>
          )}
        </div>
      </section>

      <div className="container">
        <section className="stats" aria-label="Key facts">
          <div><b>{data?.courses?.length ?? '–'}</b><span>B.Tech programmes</span></div>
          <div><b>{totalSeats || '–'}</b><span>Seats this year</span></div>
          <div><b>{data?.today?.date ? new Date(data.today.date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '–'}</b><span>College date</span></div>
        </section>

        <section id="courses" className="section">
          <h2>Courses offered</h2>
          <div className="grid">
            {(data?.courses || []).map((course) => (
              <article className="course-card" key={course.code}>
                <span className="dept">{course.department}</span>
                <h3>{course.name}</h3>
                <p className="code">{course.code}</p>
                <div className="badges">
                  <span className="badge">Fee {inr(course.annual_fee)}/yr</span>
                  <span className="badge">Hostel {inr(course.hostel_fee)}/yr</span>
                  <span className="badge badge-accent">Min 12th {course.min_12th_percent}%</span>
                  <span className="badge badge-green">{course.seats} seats</span>
                </div>
              </article>
            ))}
            {!data && !error && [1, 2, 3].map((i) => <div className="course-card skeleton" key={i} />)}
          </div>
        </section>

        <section className="strip" aria-label="Admissions information">
          <div>
            <h3>How to apply</h3>
            <p>Tell the assistant your name, phone, email, course and 12th percentage. It checks your eligibility and submits the application after you confirm.</p>
          </div>
          <div>
            <h3>Ask about</h3>
            <p>Important dates, documents needed for verification, fees, hostel and placement drives. All answers come from the official 2026 brochure and handbook.</p>
          </div>
        </section>

        <section id="talk" className="section">
          <h2>Talk to us</h2>
          <p className="muted">Ask by voice or type. Visitors never see any student data.</p>
          <VoiceAssistant mode="public" />
        </section>
      </div>
    </>
  )
}
