import { useCallback, useEffect, useState } from 'react'

/**
 * Маршруты сидят во фрагменте адреса (#/hall, #/map?u=rinh): так приложение
 * открывается одинаково и с FastAPI, и из файла сборки, и внутри Capacitor — и не надо
 * возиться с настройкой сервера под history API.
 */
export interface Route {
  path: string
  params: URLSearchParams
}

function parse(): Route {
  const raw = window.location.hash.replace(/^#/, '') || '/'
  const [path, query = ''] = raw.split('?')
  return { path: path || '/', params: new URLSearchParams(query) }
}

export function navigate(to: string, replace = false) {
  const target = to.startsWith('#') ? to : `#${to}`
  if (replace) window.history.replaceState(null, '', target)
  else window.history.pushState(null, '', target)
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute(): [Route, (to: string, replace?: boolean) => void] {
  const [route, setRoute] = useState<Route>(parse)
  useEffect(() => {
    const update = () => setRoute(parse())
    window.addEventListener('hashchange', update)
    window.addEventListener('popstate', update)
    return () => {
      window.removeEventListener('hashchange', update)
      window.removeEventListener('popstate', update)
    }
  }, [])
  return [route, useCallback((to: string, replace?: boolean) => navigate(to, replace), [])]
}
