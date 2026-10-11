import type { ThemeMode } from '../lib/theme'
import { MonitorIcon, MoonIcon, SunIcon } from './Icons'

const OPTIONS: { mode: ThemeMode; label: string; icon: JSX.Element }[] = [
  { mode: 'system', label: 'Тема как в системе', icon: <MonitorIcon /> },
  { mode: 'light', label: 'Светлая тема', icon: <SunIcon /> },
  { mode: 'dark', label: 'Тёмная тема', icon: <MoonIcon /> },
]

export default function ThemeSwitch({ mode, onChange }: { mode: ThemeMode; onChange: (mode: ThemeMode) => void }) {
  return (
    <div className="theme" role="group" aria-label="Оформление">
      {OPTIONS.map((option) => (
        <button
          key={option.mode}
          type="button"
          aria-pressed={mode === option.mode}
          aria-label={option.label}
          title={option.label}
          onClick={() => onChange(option.mode)}
        >
          {option.icon}
        </button>
      ))}
    </div>
  )
}
