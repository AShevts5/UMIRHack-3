import { useCallback, useEffect, useState } from 'react'

/** Тема интерфейса: «как в системе» (по умолчанию), светлая или тёмная — на вкус. */
export type ThemeMode = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'umir-theme'

function readStored(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

/**
 * Выбранная тема сидит в атрибуте data-theme на <html>: токены в styles.css
 * переключаются по нему, а если атрибута нет — работает prefers-color-scheme.
 * Тот же атрибут ставит скрипт в index.html ещё до первой отрисовки, чтобы ничего не мигало.
 */
export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(readStored)

  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', mode)
    try {
      if (mode === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, mode)
    } catch {
      // приватный режим браузера: тема просто не запомнится, переживём
    }
  }, [mode])

  return [mode, useCallback((next: ThemeMode) => setMode(next), [])]
}
