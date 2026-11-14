import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// States: idle -> recording -> thinking -> speaking -> idle (or error)
const LABELS = { idle: 'Tap to talk', recording: 'Listening… tap to send', thinking: 'Thinking…', speaking: 'Speaking… tap to stop', error: 'Tap to try again' }
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

function speakWithBrowser(text, onEnd) {
  if (!('speechSynthesis' in window)) return onEnd()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'en-IN'
  u.onend = onEnd
  u.onerror = onEnd
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
}

export default function VoiceAssistant({ mode = 'public' }) {
  const [status, setStatus] = useState('idle')
  const [messages, setMessages] = useState([{ role: 'assistant', content: GREETING[mode] || GREETING.public }])
  const [conversationId, setConversationId] = useState(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const recorderRef = useRef(null)
  const audioRef = useRef(null)
  const vadRef = useRef(null)
  const endRef = useRef(null)
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    if (status !== 'recording') return undefined
    setSeconds(0)
    const t = setInterval(() => setSeconds((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [status])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, status])
  useEffect(() => () => stopEverything(), [])

  function stopEverything() {
    if (vadRef.current) { vadRef.current.stop(); vadRef.current = null }
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    audioRef.current?.pause()
    window.speechSynthesis?.cancel()
  }

  function playReply(data) {
    const done = () => setStatus('idle')
    setStatus('speaking')
    const src = data.audio_url || (data.audio_b64 ? `data:audio/mpeg;base64,${data.audio_b64}` : null)
    if (src) {
      // audio_url streams from the server, so playback starts before the whole MP3 is generated
      const audio = new Audio(src)
      let fellBack = false
      audio.onerror = () => { if (!fellBack) { fellBack = true; speakWithBrowser(data.reply, done) } }
      audioRef.current = audio
      audio.onended = done
      audio.play().catch(() => { if (!fellBack) { fellBack = true; speakWithBrowser(data.reply, done) } })
    } else {
      speakWithBrowser(data.reply, done) // TTS fallback when the speech service is down
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
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (const v of buf) sum += ((v - 128) / 128) ** 2
      const rms = Math.sqrt(sum / buf.length)
      const now = performance.now()
      if (rms > 0.04) { heardSpeech = true; silentSince = now }
      if ((heardSpeech && now - silentSince > SILENCE_MS) || now - started > MAX_RECORD_MS) onSilence()
    }, 100)
    return { stop() { clearInterval(timer); ctx.close().catch(() => {}) } }
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
      setError('Microphone not available. Please allow mic access or type your question.')
      setStatus('error')
    }
  }

  function onMicClick() {
    if (status === 'recording') recorderRef.current?.stop()
    else if (status === 'speaking') { stopEverything(); setStatus('idle') }
    else if (status !== 'thinking') { stopEverything(); startRecording() }
  }

  const busy = status === 'thinking'
  return (
    <div className="assistant panel" aria-label="Voice assistant">
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

      <div className="controls">
        <button className={`mic ${status}`} onClick={onMicClick} disabled={busy} aria-label={LABELS[status]}>
          {status === 'thinking' ? <span className="spinner light" /> :
            status === 'speaking' ? <span className="eq" aria-hidden="true"><i /><i /><i /><i /></span> :
              status === 'recording' ? <span className="stop" aria-hidden="true" /> : <span aria-hidden="true">🎤</span>}
        </button>
        <div className="status" role="status">
          <b>{LABELS[status]}</b>
          {status === 'recording' && <span className="timer">0:{String(seconds).padStart(2, '0')}</span>}
          <small className="muted">{status === 'recording' ? 'Stops automatically when you pause' : 'Speech-to-text by Deepgram'}</small>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <form className="text-input" onSubmit={sendText}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Or type your question…" maxLength={2000} aria-label="Type your question" />
        <button className="btn-primary" disabled={!text.trim() || busy}>Send</button>
      </form>
    </div>
  )
}
