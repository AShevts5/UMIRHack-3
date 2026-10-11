/** Наш маленький набор иконок: всё рисуем локально, никаких внешних наборов не тащим. */

export function Logo() {
  return (
    <svg width="38" height="38" viewBox="0 0 38 38" aria-hidden="true">
      <circle cx="19" cy="19" r="17" fill="none" stroke="var(--accent)" strokeWidth="2.2" />
      <circle cx="19" cy="19" r="13" fill="none" stroke="var(--accent)" strokeWidth="1" opacity=".6" />
      <text
        x="19"
        y="23.5"
        textAnchor="middle"
        fontFamily="Onest, sans-serif"
        fontWeight="700"
        fontSize="12"
        fill="var(--accent)"
      >
        ОД
      </text>
    </svg>
  )
}

const line = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round' } as const

export function UploadIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" {...line} stroke="var(--accent)" strokeWidth="1.6" aria-hidden="true">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M12 18v-6M9 15l3-3 3 3" />
    </svg>
  )
}

export function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="1.4" aria-hidden="true">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M8.5 13h7M8.5 16.5h5" />
    </svg>
  )
}

export function LockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" {...line} strokeWidth="1.8" style={{ flex: 'none', marginTop: 2 }} aria-hidden="true">
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

export function AlertIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" {...line} strokeWidth="1.9" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5" />
      <circle cx="12" cy="16.4" r=".9" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function CheckMark() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="2.6" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}

export function BangMark() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="2.6" aria-hidden="true">
      <path d="M12 5.5v8.5" />
      <circle cx="12" cy="18.2" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="1.9" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" />
    </svg>
  )
}

export function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="1.9" aria-hidden="true">
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
    </svg>
  )
}

export function MonitorIcon() {
  return (
    <svg viewBox="0 0 24 24" {...line} strokeWidth="1.9" aria-hidden="true">
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8.5 20h7M12 16v4" />
    </svg>
  )
}
