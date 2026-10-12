import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import type { Session } from './api/guide'
import { guideApi, storeToken } from './api/guide'
import Assistant from './components/Assistant'
import BadgeToast from './components/BadgeToast'
import Footer from './components/Footer'
import { ArrowLeftIcon, InstallIcon, UserIcon } from './components/GuideIcons'
import { Logo } from './components/Icons'
import ThemeSwitch from './components/ThemeSwitch'
import { useInstallPrompt } from './lib/pwa'
import { useRoute } from './lib/router'
import { GuideProvider, useGuide } from './lib/session'
import { useTheme } from './lib/theme'
import AuthScreen from './screens/AuthScreen'
import EnterTransition from './screens/EnterTransition'
import Hall from './screens/Hall'

// Карта тащит за собой Leaflet — поэтому грузим её, только когда открыли раздел. Остальные
// разделы тоже отдельными чанками: так вход и холл открываются шустрее, особенно на телефоне.
const MapScreen = lazy(() => import('./screens/MapScreen'))
const App = lazy(() => import('./App'))
const Faculties = lazy(() => import('./screens/Faculties'))
const History = lazy(() => import('./screens/History'))
const Profile = lazy(() => import('./screens/Profile'))

// Как только браузер освободится, подгружаем разделы заранее: переходы мгновенные, а service
// worker складывает чанки в кеш, и справка с картой потом открываются даже без сети.
function prefetchSections() {
  const load = () => {
    void import('./App')
    void import('./screens/Faculties')
    void import('./screens/History')
    void import('./screens/Profile')
    void import('./screens/MapScreen')
  }
  const idle = (window as Window & { requestIdleCallback?: (cb: () => void) => number }).requestIdleCallback
  if (idle) idle(load)
  else window.setTimeout(load, 1500)
}

const PUBLIC_PATHS = new Set(['/login', '/reset', '/auth'])
const TITLES: Record<string, string> = {
  '/login': 'Вход',
  '/reset': 'Новый пароль',
  '/hall': 'Холл',
  '/dean': 'Обращение в деканат',
  '/faculties': 'Факультеты и корпуса',
  '/map': 'Карта вузов',
  '/history': 'История обращений',
  '/profile': 'Профиль',
}
const NAV: [string, string][] = [
  ['/hall', 'Холл'],
  ['/faculties', 'Факультеты'],
  ['/map', 'Карта'],
  ['/history', 'История'],
]

function UserChip({ go }: { go: (to: string) => void }) {
  const { user } = useGuide()
  return user ? (
    <button type="button" className="user-chip" onClick={() => go('/profile')} title="Профиль">
      <UserIcon />
      <span>{user.name ?? user.login ?? user.email}</span>
    </button>
  ) : (
    <button type="button" className="user-chip" onClick={() => go('/login')}>
      <UserIcon />
      <span>Войти</span>
    </button>
  )
}

/** Плашка «нет сети»: PWA и офлайн откроется, но для проверки и входа нужен сервер. */
function OfflineBanner() {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])
  if (online) return null
  return (
    <div className="offline" role="status">
      Нет сети. Холл, справка по факультетам и список вузов открыты из сохранённой копии; проверка документов, вход и
      помощник заработают, когда связь вернётся.
    </div>
  )
}

function Shell() {
  const [route, go] = useRoute()
  const guide = useGuide()
  const { ready, user, guest, university, signIn, continueAsGuest } = guide
  const [theme, setTheme] = useTheme()
  const [entering, setEntering] = useState(false)
  const install = useInstallPrompt()
  const path = route.path === '/' ? '/hall' : route.path

  // вернулись из Яндекс ID: токен прилетает во фрагменте адреса
  useEffect(() => {
    if (path !== '/auth') return
    const token = route.params.get('token')
    if (!token) {
      go('/login', true)
      return
    }
    storeToken(token)
    guideApi
      .me()
      .then((me) => {
        signIn({ access_token: token, user: me })
        setEntering(true)
        go('/hall', true)
      })
      .catch(() => {
        storeToken(null)
        go('/login?error=' + encodeURIComponent('Не удалось завершить вход через Яндекс.'), true)
      })
  }, [go, path, route.params, signIn])

  useEffect(prefetchSections, [])

  const mainRef = useRef<HTMLElement | null>(null)
  const firstPath = useRef(true)
  useEffect(() => {
    window.scrollTo({ top: 0 })
    // при переходе между разделами экранный диктор начинает читать с нового содержимого
    if (firstPath.current) firstPath.current = false
    else mainRef.current?.focus({ preventScroll: true })
  }, [path])

  const signedOut = ready && !user && !guest && !PUBLIC_PATHS.has(path)
  useEffect(() => {
    document.title = `${TITLES[signedOut ? '/login' : path] ?? 'Холл'} · Академиум`
  }, [path, signedOut])

  const onSignedIn = useCallback(
    (session: Session) => {
      signIn(session)
      setEntering(true)
      go('/hall', true)
    },
    [go, signIn],
  )

  const finishEnter = useCallback(() => setEntering(false), [])

  if (!ready) return <div className="splash" aria-busy="true" />

  const needsLogin = !user && !guest && !PUBLIC_PATHS.has(path)
  const showAuth = needsLogin || path === '/login' || path === '/reset'

  if (path === '/auth') return <div className="splash" aria-busy="true" />

  if (showAuth) {
    return (
      <>
        <AuthScreen
          key={path + route.params.toString()}
          initialError={route.params.get('error')}
          challenge={route.params.get('challenge')}
          resetToken={path === '/reset' ? route.params.get('token') : null}
          onSignedIn={onSignedIn}
          onGuest={() => {
            continueAsGuest()
            setEntering(true)
            go('/hall', true)
          }}
        />
        <Assistant />
        <BadgeToast />
        <OfflineBanner />
      </>
    )
  }

  const building = university?.hall?.building_image ?? '/universities/_default/building.svg'
  const hall = university?.hall?.hall_image ?? '/universities/_default/hall.svg'

  if (path === '/dean') {
    return (
      <>
        <Suspense fallback={<div className="splash" aria-busy="true" />}>
          <App
            theme={theme}
            onTheme={setTheme}
            initialRule={route.params.get('rule')}
            universityTitle={university ? `Обращение в деканат · ${university.short_name}` : undefined}
            leading={
              <button
                type="button"
                className="icon-btn back-btn"
                aria-label="В холл"
                title="В холл"
                onClick={() => go('/hall')}
              >
                <ArrowLeftIcon />
              </button>
            }
            trailing={<UserChip go={go} />}
          />
        </Suspense>
        <Footer />
        <Assistant />
        <BadgeToast />
        <OfflineBanner />
      </>
    )
  }

  let screen
  if (path === '/faculties') screen = <Faculties go={go} />
  else if (path === '/map')
    screen = (
      <Suspense fallback={<p className="guide-empty">Загружаем карту…</p>}>
        <MapScreen go={go} params={route.params} />
      </Suspense>
    )
  else if (path === '/history') screen = <History go={go} />
  else if (path === '/profile') screen = <Profile go={go} params={route.params} />
  else screen = <Hall go={go} />

  const isHall = !['/faculties', '/map', '/history', '/profile'].includes(path)

  return (
    <>
      {entering ? (
        <EnterTransition building={building} hall={hall} doors={university?.hall_doors} onDone={finishEnter} />
      ) : null}
      <button type="button" className="skip" onClick={() => mainRef.current?.focus()}>
        К содержимому
      </button>
      <header className="bar">
        <div className="bar__in">
          <button type="button" className="brand brand--btn" onClick={() => go('/hall')}>
            <Logo />
            <div>
              <b>Академиум</b>
              <small>{university ? `Онлайн-деканат · ${university.short_name}` : 'Онлайн-деканат и путеводитель по вузу'}</small>
            </div>
          </button>
          <nav className="seg" aria-label="Разделы">
            {NAV.map(([to, label]) => (
              <button
                key={to}
                type="button"
                aria-pressed={to === '/hall' ? isHall : path === to}
                onClick={() => go(to)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="bar__tools">
            {install ? (
              <button type="button" className="btn btn--quiet" onClick={install} title="Установить приложение">
                <InstallIcon /> Установить
              </button>
            ) : null}
            <ThemeSwitch mode={theme} onChange={setTheme} />
            <UserChip go={go} />
          </div>
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1} className={isHall ? 'guide guide--hall' : 'guide wrap'}>
        <Suspense fallback={<p className="guide-empty">Загружаем…</p>}>{screen}</Suspense>
      </main>
      <Footer />
      <Assistant />
      <BadgeToast />
      <OfflineBanner />
    </>
  )
}

export default function Root() {
  return (
    <GuideProvider>
      <Shell />
    </GuideProvider>
  )
}
