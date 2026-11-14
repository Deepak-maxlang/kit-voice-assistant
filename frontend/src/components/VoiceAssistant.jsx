import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import AvatarVideo from './AvatarVideo.jsx'

// States: idle -> recording -> thinking -> speaking -> idle (or error)
const LABELS = { idle: 'Tap to talk', recording: 'Listening…', thinking: 'Thinking…', speaking: 'Speaking…', error: 'Tap to try again' }
const GREETING = {
  public: 'Hello! I can help with admissions at KIT: courses, fees, eligibility and applying. What would you like to know?',
  student: 'Hi! Ask me about your attendance, timetable, leave or on-duty requests, placements, rules or DBMS doubts.',
  teacher: 'Hello! I can show your classes and attendance, and approve or reject leave and on-duty requests.',
}
const CHIPS = {
  public: ['Fees for CSE', 'Am I eligible with 72% for CSE?', 'Apply for admission', 'Placement drives', 'Documents needed'],
  student: ['My attendance', 'Apply leave', 'On-duty request', 'Placement drives', 'DBMS doubt: what is BCNF?'],
  teacher: ['Pending requests', 'Class attendance', 'My classes'],
}
const SILENCE_MS = 1300
const MAX_RECORD_MS = 20000
const DEMO_STATES = new Set(['idle', 'recording', 'thinking', 'speaking', 'error'])

function speakWithBrowser(text, onEnd) {
  if (!('speechSynthesis' in window)) return onEnd()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'en-IN'
  u.onend = onEnd
  u.onerror = onEnd
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
}

function rmsFromAnalyser(analyser, buf) {
  analyser.getByteTimeDomainData(buf)
  let sum = 0
  for (const v of buf) sum += ((v - 128) / 128) ** 2
  return Math.sqrt(sum / buf.length)
}

function AssistantOrb({ status, level, onClick, disabled, label }) {
  return (
    <button
      type="button"
      className={`orb ${status}`}
      style={{ '--level': Math.max(0, Math.min(1, level || 0)) }}
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
    >
      <span className="orb-rings" aria-hidden="true"><i /><i /><i /></span>
      <span className="orb-core" aria-hidden="true">
        <span className="orb-highlight" />
        <span className="orb-shimmer" />
      </span>
      <span className="orb-orbit" aria-hidden="true"><i /><i /><i /></span>
    </button>
  )
}

export default function VoiceAssistant({ mode = 'public' }) {
  const [status, setStatus] = useState('idle')
  const [messages, setMessages] = useState([{ role: 'assistant', content: GREETING[mode] || GREETING.public }])
  const [conversationId, setConversationId] = useState(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [level, setLevel] = useState(0)
  const [demo, setDemo] = useState(null)
  const recorderRef = useRef(null)
  const audioRef = useRef(null)
  const vadRef = useRef(null)
  const playbackRef = useRef(null)
  const endRef = useRef(null)
  const panelRef = useRef(null)
  const welcomedRef = useRef(false)
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const v = new URLSearchParams(window.location.search).get('orb')
    if (DEMO_STATES.has(v)) setDemo(v)
  }, [])

  useEffect(() => {
    if (status !== 'recording') return undefined
    setSeconds(0)
    const t = setInterval(() => setSeconds((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [status])

  useEffect(() => { const box = endRef.current?.parentElement; if (box) box.scrollTop = box.scrollHeight }, [messages, status])
  useEffect(() => () => stopEverything(), [])

  // Spoken welcome: once per page load, when the panel scrolls into view. Browsers block audio until the
  // user interacts, so if play() is refused we queue it for the first click/tap/key anywhere on the page.
  useEffect(() => {
    const el = panelRef.current
    if (!el || welcomedRef.current) return undefined
    let cleanupGesture = () => {}
    const greet = () => {
      if (welcomedRef.current) return
      welcomedRef.current = true
      const audio = new Audio(`/api/welcome-audio?mode=${encodeURIComponent(mode)}`)
      const done = () => { setStatus((s) => (s === 'speaking' ? 'idle' : s)); stopLevelWatch() }
      audio.onended = done
      audio.onerror = done
      audioRef.current = audio
      audio.play().then(() => setStatus('speaking')).catch(() => {
        welcomedRef.current = false // autoplay blocked: wait for the first user gesture
        const onGesture = (e) => {
          cleanupGesture()
          // If the first tap is the talk button, the user wants to speak, not hear the greeting.
          if (e.target?.closest?.('.dock-orb, .status-btn, form')) { welcomedRef.current = true; return }
          greet()
        }
        const evs = ['pointerdown', 'keydown']
        evs.forEach((ev) => document.addEventListener(ev, onGesture, { capture: true }))
        cleanupGesture = () => evs.forEach((ev) => document.removeEventListener(ev, onGesture, { capture: true }))
      })
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting)) { io.disconnect(); greet() }
    }, { threshold: 0.4 })
    io.observe(el)
    return () => { io.disconnect(); cleanupGesture() }
  }, [mode])

  function stopLevelWatch() {
    if (playbackRef.current) { playbackRef.current.stop(); playbackRef.current = null }
    setLevel(0)
  }

  function stopEverything() {
    if (vadRef.current) { vadRef.current.stop(); vadRef.current = null }
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    audioRef.current?.pause()
    window.speechSynthesis?.cancel()
    stopLevelWatch()
  }

  function watchAnalyser(analyser, ctx, scale = 4) {
    const buf = new Uint8Array(analyser.fftSize)
    const timer = setInterval(() => {
      setLevel(Math.min(1, rmsFromAnalyser(analyser, buf) * scale))
    }, 80)
    return { stop() { clearInterval(timer); setLevel(0); ctx.close().catch(() => {}) } }
  }

  function watchPlayback(audio) {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return { stop() {} }
    try {
      const ctx = new Ctx()
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      const src = ctx.createMediaElementSource(audio)
      src.connect(analyser)
      analyser.connect(ctx.destination)
      ctx.resume().catch(() => {})
      return watchAnalyser(analyser, ctx, 5)
    } catch {
      return { stop() { setLevel(0) } }
    }
  }

  function playReply(data) {
    const done = () => { stopLevelWatch(); setStatus('idle') }
    setStatus('speaking')
    const src = data.audio_url || (data.audio_b64 ? `data:audio/mpeg;base64,${data.audio_b64}` : null)
    if (src) {
      const audio = new Audio(src)
      let fellBack = false
      audio.onerror = () => { if (!fellBack) { fellBack = true; stopLevelWatch(); speakWithBrowser(data.reply, done) } }
      audioRef.current = audio
      audio.onended = done
      playbackRef.current = watchPlayback(audio)
      audio.play().catch(() => { if (!fellBack) { fellBack = true; stopLevelWatch(); speakWithBrowser(data.reply, done) } })
    } else {
      speakWithBrowser(data.reply, done)
    }
  }

  function handleResult(data) {
    if (data.conversation_id) setConversationId(data.conversation_id)
    setMessages((m) => [
      ...m,
      ...(data.transcript ? [{ role: 'user', content: data.transcript }] : []),
      { role: 'assistant', content: data.reply, actions: data.actions },
    ])
    playReply(data)
  }

  async function sendAudio(blob) {
    setStatus('thinking')
    stopLevelWatch()
    try {
      handleResult(await api.voice(blob, conversationId))
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  async function sendText(e, preset) {
    e?.preventDefault()
    const msg = (preset ?? text).trim()
    if (!msg || status === 'thinking') return
    stopEverything()
    setText('')
    setMessages((m) => [...m, { role: 'user', content: msg }])
    setStatus('thinking')
    try {
      const data = await api.chat(msg, conversationId, true)
      handleResult({ ...data, transcript: '' })
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }

  function startSilenceDetection(stream, onSilence) {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return { stop() {} }
    const ctx = new Ctx()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    ctx.createMediaStreamSource(stream).connect(analyser)
    const buf = new Uint8Array(analyser.fftSize)
    let heardSpeech = false
    let silentSince = performance.now()
    const started = performance.now()
    const timer = setInterval(() => {
      const rms = rmsFromAnalyser(analyser, buf)
      setLevel(Math.min(1, rms * 6))
      const now = performance.now()
      if (rms > 0.04) { heardSpeech = true; silentSince = now }
      if ((heardSpeech && now - silentSince > SILENCE_MS) || now - started > MAX_RECORD_MS) onSilence()
    }, 100)
    return { stop() { clearInterval(timer); setLevel(0); ctx.close().catch(() => {}) } }
  }

  async function startRecording() {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      const chunks = []
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        if (vadRef.current) { vadRef.current.stop(); vadRef.current = null }
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' })
        if (blob.size > 1000) sendAudio(blob)
        else setStatus('idle')
      }
      recorderRef.current = recorder
      recorder.start()
      vadRef.current = startSilenceDetection(stream, () => recorder.state === 'recording' && recorder.stop())
      setStatus('recording')
    } catch (e) {
      setError('Microphone is blocked. Allow the microphone in the address bar, or type below.')
      setStatus('error')
    }
  }

  function onOrbClick() {
    if (status === 'recording') recorderRef.current?.stop()
    else if (status === 'speaking') { stopEverything(); setStatus('idle') }
    else if (status !== 'thinking') { stopEverything(); startRecording() }
  }

  const viewStatus = demo || status
  const busy = status === 'thinking' || viewStatus === 'thinking'
  const label = LABELS[viewStatus]
  return (
    <div className="assistant panel" aria-label="Voice assistant" ref={panelRef}>
      <div className="assistant-head">
        <span className="dot" aria-hidden="true" /> <b>KIT Assistant</b>
        <span className="muted small">{mode === 'public' ? 'Admissions help' : `${mode} services`}</span>
      </div>
      <div className="messages" aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            <span>{m.content}</span>
            {m.actions?.length > 0 && (
              <small className="actions">{m.actions.map((a) => `${a.tool.replaceAll('_', ' ')}: ${a.status.replaceAll('_', ' ')}`).join(' · ')}</small>
            )}
          </div>
        ))}
        {busy && <div className="bubble assistant typing" aria-label="Thinking"><i /><i /><i /></div>}
        <div ref={endRef} />
      </div>

      <div className="chips" aria-label="Suggestions">
        {(CHIPS[mode] || CHIPS.public).map((c) => (
          <button key={c} type="button" className="chip" disabled={busy} onClick={() => sendText(null, c)}>{c}</button>
        ))}
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      <div className="dock">
        <div className="dock-orb">
          <AvatarVideo
            status={viewStatus}
            onClick={onOrbClick}
            disabled={status === 'thinking'}
            label={label}
            fallback={<AssistantOrb status={viewStatus} level={demo ? 0.45 : level} onClick={onOrbClick} disabled={status === 'thinking'} label={label} />}
          />
          <button type="button" className={`status-btn ${viewStatus}`} onClick={onOrbClick} disabled={status === 'thinking'} role="status">
            <b>{label}</b>
            {viewStatus === 'recording' && <span className="timer">0:{String(seconds).padStart(2, '0')}</span>}
            {viewStatus === 'speaking' && <small className="muted">tap to stop</small>}
          </button>
        </div>
        <div className="dock-main">
          <form className="text-input" onSubmit={sendText}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Or type your question…" maxLength={2000} aria-label="Type your question" />
            <button className="btn-primary" disabled={!text.trim() || busy}>Send</button>
          </form>
        </div>
      </div>
    </div>
  )
}
