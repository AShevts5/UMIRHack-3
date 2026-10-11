/** Иконки путеводителя (вход, холл, карта, помощник). Рисуем сами, локально. */

const line = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round', strokeWidth: 1.7 } as const

type P = { size?: number }

export function YandexIcon({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#FC3F1D" />
      <path d="M13.3 6.2h-1.1c-2 0-3.1 1-3.1 2.6 0 1.8.8 2.6 2.4 3.7l1.3.9-3.7 5.4H6.3l3.3-4.9C7.7 12.6 6.6 11.3 6.6 9c0-2.8 1.9-4.7 5.6-4.7h3.6v13.6h-2.5z" fill="#fff" />
    </svg>
  )
}

export function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {open ? null : <path d="M4 20 20 4" />}
    </svg>
  )
}

export function RobotIcon({ size = 22 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <rect x="4" y="8" width="16" height="11" rx="3" />
      <path d="M12 4v4M9 13h.01M15 13h.01M9.5 16.2h5M2 12.5v3M22 12.5v3" />
    </svg>
  )
}

export function ChatIcon({ size = 26 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M20 11.5a7.5 7.5 0 0 1-11 6.6L4 19.5l1.4-4.3A7.5 7.5 0 1 1 20 11.5z" />
    </svg>
  )
}

export function CloseIcon({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} strokeWidth={2} aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  )
}

export function MicIcon({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
    </svg>
  )
}

export function SpeakerIcon({ size = 18, muted = false }: P & { muted?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      {muted ? <path d="M16 9.5l5 5M21 9.5l-5 5" /> : <path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11" />}
    </svg>
  )
}

export function ArrowLeftIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} strokeWidth={2} aria-hidden="true">
      <path d="M19 12H5M11 6l-6 6 6 6" />
    </svg>
  )
}

export function SearchIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </svg>
  )
}

export function UserIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <circle cx="12" cy="8.5" r="3.8" />
      <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
    </svg>
  )
}

export function DeanIcon({ size = 30 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 14l2 2 4-4.5" />
    </svg>
  )
}

export function FacultyIcon({ size = 30 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M3 9.5 12 4l9 5.5M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20.5h18" />
    </svg>
  )
}

export function MapIcon({ size = 30 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6z" />
      <path d="M9 4v14M15 6v14" />
    </svg>
  )
}

export function HistoryIcon({ size = 30 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5" />
      <path d="M3.5 4v4.5H8M12 7.5V12l3 2" />
    </svg>
  )
}

export function PinIcon({ size = 16 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M12 21s-6.5-6.2-6.5-11.2a6.5 6.5 0 0 1 13 0C18.5 14.8 12 21 12 21z" />
      <circle cx="12" cy="9.8" r="2.3" />
    </svg>
  )
}

export function InstallIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" />
    </svg>
  )
}

export function ShieldIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...line} aria-hidden="true">
      <path d="M12 3 4.5 6v5.5c0 4.5 3.2 8 7.5 9.5 4.3-1.5 7.5-5 7.5-9.5V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}
