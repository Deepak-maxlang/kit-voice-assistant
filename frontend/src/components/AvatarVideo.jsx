import { useEffect, useRef, useState } from 'react'

// Optional talking avatar. Drop these files into frontend/public/avatar/ to enable it:
//   speaking.mp4 (loops only while reply audio is audible), welcome.mp4 (kept for future use),
//   idle.jpg (poster, shown otherwise). All silent; videos are muted + playsInline so they autoplay on mobile.
// If any file is missing, `fallback` (the animated orb) is rendered instead.
const FILES = { welcome: '/avatar/welcome.mp4', speaking: '/avatar/speaking.mp4', idle: '/avatar/idle.jpg' }
let probe = null // shared across mounts: one set of HEAD requests per page load

async function exists(url, kind) {
  try {
    const r = await fetch(url, { method: 'HEAD', cache: 'no-store' })
    // The SPA fallback returns index.html (200, text/html) for unknown paths, so check the content type too.
    return r.ok && (r.headers.get('content-type') || '').startsWith(kind)
  } catch {
    return false
  }
}

export function avatarAvailable() {
  if (!probe) {
    probe = Promise.all([exists(FILES.speaking, 'video'), exists(FILES.idle, 'image')])
      .then((r) => r.every(Boolean))
  }
  return probe
}

export default function AvatarVideo({ status, talking, onClick, disabled, label, fallback }) {
  const [ready, setReady] = useState(false)
  const videoRef = useRef(null)

  useEffect(() => {
    let alive = true
    avatarAvailable().then((ok) => alive && setReady(ok))
    return () => { alive = false }
  }, [])

  // Idle still by default. The talking clip runs ONLY while reply/welcome audio is audibly playing
  // (`talking` follows the audio element's playing/pause/ended/error events), so it never lip-syncs silently.
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    if (talking) { v.currentTime = 0; v.play().catch(() => {}) } else { v.pause(); v.currentTime = 0 }
  }, [talking, ready])

  // Listening/thinking keep the animated orb (clear state feedback); the avatar is the face for idle/speaking.
  if (!ready || status === 'recording' || status === 'thinking') return fallback

  return (
    <button type="button" className={`avatar ${status}`} onClick={onClick} disabled={disabled} aria-label={label}>
      <img className="avatar-media" src={FILES.idle} alt="" aria-hidden="true" />
      <video
        ref={videoRef}
        className={`avatar-media avatar-video ${talking ? 'on' : ''}`}
        src={FILES.speaking}
        poster={FILES.idle}
        muted
        playsInline
        loop
        preload="auto"
        aria-hidden="true"
      />
      <span className="avatar-ring" aria-hidden="true" />
    </button>
  )
}
