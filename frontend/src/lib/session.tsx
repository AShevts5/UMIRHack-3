import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { AppConfig, Session, University, UniversitySummary, User } from '../api/guide'
import { SESSION_EXPIRED_EVENT, guideApi, readToken, readUniversity, storeToken, storeUniversity } from '../api/guide'
import { navigate } from './router'

const GUEST_KEY = 'umir-guest'

interface GuideState {
  config: AppConfig | null
  universities: UniversitySummary[] | null
  university: University | null
  universityId: string
  user: User | null
  /** токен проверили (или его вообще нет) — теперь можно решать, показывать ли вход */
  ready: boolean
  guest: boolean
  offline: boolean
  signIn: (session: Session) => void
  signOut: () => void
  continueAsGuest: () => void
  setUser: (user: User) => void
  chooseUniversity: (id: string) => Promise<void>
}

const GuideContext = createContext<GuideState | null>(null)

function readGuest(): boolean {
  try {
    return sessionStorage.getItem(GUEST_KEY) === '1'
  } catch {
    return false
  }
}

export function GuideProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [universities, setUniversities] = useState<UniversitySummary[] | null>(null)
  const [university, setUniversity] = useState<University | null>(null)
  const [universityId, setUniversityId] = useState<string>(() => readUniversity() ?? '')
  const [user, setUserState] = useState<User | null>(null)
  const [ready, setReady] = useState(false)
  const [guest, setGuest] = useState(readGuest)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    guideApi
      .config()
      .then((payload) => {
        setConfig(payload)
        setUniversityId((current) => current || payload.default_university || 'rinh')
      })
      .catch(() => {
        setOffline(true)
        setUniversityId((current) => current || 'rinh')
      })
    guideApi
      .universities()
      .then(setUniversities)
      .catch(() => setUniversities([]))
    if (!readToken()) {
      setReady(true)
      return
    }
    guideApi
      .me()
      .then((me) => {
        setUserState(me)
        if (me.university_id) setUniversityId(me.university_id)
      })
      .catch(() => storeToken(null))
      .finally(() => setReady(true))
  }, [])

  useEffect(() => {
    if (!universityId) return
    storeUniversity(universityId)
    let cancelled = false
    guideApi
      .university(universityId)
      .then((payload) => !cancelled && setUniversity(payload))
      .catch(() => !cancelled && setUniversity(null))
    return () => {
      cancelled = true
    }
  }, [universityId])

  const signIn = useCallback((session: Session) => {
    storeToken(session.access_token)
    setUserState(session.user)
    if (session.user.university_id) setUniversityId(session.user.university_id)
    try {
      sessionStorage.removeItem(GUEST_KEY)
    } catch {
      // ну и ладно, не критично
    }
    setGuest(false)
  }, [])

  const signOut = useCallback(() => {
    storeToken(null)
    setUserState(null)
    setGuest(false)
    try {
      sessionStorage.removeItem(GUEST_KEY)
    } catch {
      // ну и ладно, не критично
    }
  }, [])

  useEffect(() => {
    const onExpired = () => {
      if (!readToken()) return
      signOut()
      navigate('/login?error=' + encodeURIComponent('Сессия истекла, войдите ещё раз.'), true)
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [signOut])

  const continueAsGuest = useCallback(() => {
    try {
      sessionStorage.setItem(GUEST_KEY, '1')
    } catch {
      // ну и ладно, не критично
    }
    setGuest(true)
  }, [])

  const chooseUniversity = useCallback(
    async (id: string) => {
      setUniversityId(id)
      if (user) {
        try {
          setUserState(await guideApi.updateMe({ university_id: id }))
        } catch {
          // выбор просто останется в браузере
        }
      }
    },
    [user],
  )

  const value = useMemo<GuideState>(
    () => ({
      config,
      universities,
      university,
      universityId,
      user,
      ready,
      guest,
      offline,
      signIn,
      signOut,
      continueAsGuest,
      setUser: setUserState,
      chooseUniversity,
    }),
    [config, universities, university, universityId, user, ready, guest, offline, signIn, signOut, continueAsGuest, chooseUniversity],
  )

  return <GuideContext.Provider value={value}>{children}</GuideContext.Provider>
}

export function useGuide(): GuideState {
  const value = useContext(GuideContext)
  if (!value) throw new Error('useGuide вне GuideProvider')
  return value
}
