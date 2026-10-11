import { useEffect, useState } from 'react'

/** Регистрируем service worker: офлайн-оболочка и установка на рабочий стол (PWA). */
export function registerServiceWorker() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  // В мобильной сборке (Capacitor) файлы и так внутри приложения, SW там не нужен.
  if ((window as unknown as { Capacitor?: unknown }).Capacitor) return
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => undefined)
  })
}

interface InstallEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

/** Кнопка «Установить»: браузер кидает beforeinstallprompt, если приложение вообще можно поставить. */
export function useInstallPrompt(): (() => Promise<void>) | null {
  const [event, setEvent] = useState<InstallEvent | null>(null)
  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault()
      setEvent(e as InstallEvent)
    }
    const onInstalled = () => setEvent(null)
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])
  if (!event) return null
  return async () => {
    await event.prompt()
    await event.userChoice.catch(() => undefined)
    setEvent(null)
  }
}
