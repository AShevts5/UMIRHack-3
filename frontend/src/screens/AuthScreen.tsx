import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../api/client'
import type { Session } from '../api/guide'
import { guideApi, yandexLoginUrl } from '../api/guide'
import { EyeIcon, YandexIcon } from '../components/GuideIcons'
import { useGuide } from '../lib/session'

type Mode = 'login' | 'register' | 'forgot' | 'twofa' | 'reset'

interface Props {
  initialMode?: Mode
  initialError?: string | null
  challenge?: string | null
  resetToken?: string | null
  onSignedIn: (session: Session) => void
  onGuest: () => void
}

function message(reason: unknown): string {
  return reason instanceof ApiError ? reason.message : 'Не удалось связаться с сервером.'
}

/**
 * Экран входа: слева картинка главного корпуса вуза (файл берём из конфига вуза),
 * справа окно входа в нашем тёмном стиле. На телефоне здание ужимается в узкую полоску сверху.
 */
export default function AuthScreen({ initialMode = 'login', initialError, challenge: initialChallenge, resetToken, onSignedIn, onGuest }: Props) {
  const { university, config, universities, universityId, chooseUniversity } = useGuide()
  const [mode, setMode] = useState<Mode>(initialChallenge ? 'twofa' : resetToken ? 'reset' : initialMode)
  const [login, setLogin] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [challenge, setChallenge] = useState<string | null>(initialChallenge ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(initialError ?? null)
  const [info, setInfo] = useState<string | null>(null)

  useEffect(() => {
    setError(initialError ?? null)
  }, [initialError])

  const building = university?.hall?.building_image ?? '/universities/_default/building.svg'
  const caption = university?.hall?.building_caption ?? university?.name ?? ''
  const yandexReady = config?.auth.yandex ?? false

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setInfo(null)
    setPassword('')
    setPassword2('')
    setCode('')
  }

  async function demoLogin() {
    setError(null)
    setBusy(true)
    try {
      onSignedIn(await guideApi.demo())
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.')
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      if (mode === 'login') {
        const result = await guideApi.login(login, password)
        if ('two_factor_required' in result) {
          setChallenge(result.challenge)
          setMode('twofa')
          setPassword('')
        } else onSignedIn(result)
      } else if (mode === 'twofa' && challenge) {
        onSignedIn(await guideApi.secondFactor(challenge, code))
      } else if (mode === 'register') {
        if (password !== password2) throw new ApiError('Пароли не совпадают.', 422)
        const looksLikeEmail = login.includes('@')
        onSignedIn(
          await guideApi.register({
            login: looksLikeEmail ? undefined : login || undefined,
            email: looksLikeEmail ? login : email || undefined,
            name: name || undefined,
            password,
            university_id: university?.id,
          }),
        )
      } else if (mode === 'forgot') {
        setInfo((await guideApi.forgot(login)).message)
      } else if (mode === 'reset' && resetToken) {
        if (password !== password2) throw new ApiError('Пароли не совпадают.', 422)
        onSignedIn(await guideApi.reset(resetToken, password))
      }
    } catch (reason) {
      setError(message(reason))
      if (mode === 'twofa' && reason instanceof ApiError && reason.status === 401 && /токен|сесси/i.test(reason.message)) {
        setMode('login')
      }
    } finally {
      setBusy(false)
    }
  }

  const titles: Record<Mode, string> = {
    login: 'Вход в систему',
    register: 'Регистрация',
    forgot: 'Восстановление пароля',
    twofa: 'Подтверждение входа',
    reset: 'Новый пароль',
  }

  const passwordField = (id: string, label: string, value: string, set: (v: string) => void, autoComplete: string) => (
    <label className="auth__field" htmlFor={id}>
      <span>{label}</span>
      <span className="auth__pw">
        <input
          id={id}
          type={showPassword ? 'text' : 'password'}
          value={value}
          onChange={(event) => set(event.target.value)}
          placeholder="••••••••"
          autoComplete={autoComplete}
          minLength={mode === 'login' ? undefined : 8}
          required
        />
        <button
          type="button"
          className="auth__eye"
          aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
          onClick={() => setShowPassword((v) => !v)}
        >
          <EyeIcon open={showPassword} />
        </button>
      </span>
    </label>
  )

  return (
    <div className="auth force-dark">
      {/* чисто декор: искорки и самолётик летают по фону, скринридеру это не нужно */}
      <div className="auth__deco" aria-hidden="true">
        <i className="auth__spark auth__spark--1" />
        <i className="auth__spark auth__spark--2" />
        <i className="auth__spark auth__spark--3" />
        <i className="auth__spark auth__spark--4" />
        <i className="auth__plane" />
      </div>
      <div className="auth__stage">
        <figure className="auth__art">
          <img src={building} alt={university?.hall?.building_caption ?? 'Главный корпус университета'} />
          <figcaption>
            <b>{university?.short_name ?? 'Онлайн-деканат'}</b>
            <span>{caption}</span>
          </figcaption>
          {universities && universities.length > 1 ? (
            <label className="auth__uni">
              <span>Ваш вуз</span>
              <select value={universityId} onChange={(event) => chooseUniversity(event.target.value)}>
                {universities.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.short_name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </figure>
      </div>

      <div className="auth__side">
        <div className="auth__brand">
          <img src="/brand/academium-mark.webp" alt="" width={84} height={67} />
          <div>
            <b>Академиум</b>
            <span>Онлайн-деканат: справки, заявления и путеводитель по вузу</span>
          </div>
        </div>
        <form className="auth__card" onSubmit={submit} noValidate={false}>
          <h1>{titles[mode]}</h1>

          {mode === 'login' || mode === 'register' ? (
            <>
              <a
                className={yandexReady ? 'auth__oauth' : 'auth__oauth is-off'}
                href={yandexReady ? yandexLoginUrl : undefined}
                aria-disabled={!yandexReady}
                onClick={(event) => {
                  if (!yandexReady) {
                    event.preventDefault()
                    setError('Вход через Яндекс не настроен на этом сервере: администратор не задал ключи приложения.')
                  }
                }}
              >
                <YandexIcon />
                Войти через Яндекс
              </a>
              {config?.auth.demo && mode === 'login' ? (
                <button type="button" className="btn auth__demo" disabled={busy} onClick={demoLogin}>
                  Войти в демо-аккаунт для жюри
                </button>
              ) : null}
              <div className="auth__or">
                <span>или</span>
              </div>
            </>
          ) : null}

          {mode === 'twofa' ? (
            <p className="auth__hint">
              {recovery
                ? 'Введите один из кодов восстановления, которые вы сохранили при включении 2FA. Каждый код срабатывает один раз.'
                : 'Введите шестизначный код из приложения-аутентификатора. Код меняется каждые 30 секунд.'}
            </p>
          ) : null}
          {mode === 'forgot' ? (
            <p className="auth__hint">Укажите логин или email, с которым вы регистрировались.</p>
          ) : null}

          {mode === 'login' || mode === 'register' || mode === 'forgot' ? (
            <label className="auth__field" htmlFor="auth-login">
              <span>{mode === 'register' ? 'Логин или email' : 'Логин или email'}</span>
              <input
                id="auth-login"
                value={login}
                onChange={(event) => setLogin(event.target.value)}
                placeholder="ivanov или ivanov@mail.ru"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
              />
            </label>
          ) : null}

          {mode === 'register' && !login.includes('@') ? (
            <label className="auth__field" htmlFor="auth-email">
              <span>Email (необязательно)</span>
              <input
                id="auth-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="ivanov@mail.ru"
                autoComplete="email"
              />
            </label>
          ) : null}

          {mode === 'register' ? (
            <label className="auth__field" htmlFor="auth-name">
              <span>Как к вам обращаться</span>
              <input id="auth-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Иван" autoComplete="given-name" />
            </label>
          ) : null}

          {mode === 'login' ? passwordField('auth-pass', 'Пароль', password, setPassword, 'current-password') : null}
          {mode === 'register' || mode === 'reset' ? (
            <>
              {passwordField('auth-pass', 'Пароль (от 8 символов)', password, setPassword, 'new-password')}
              {passwordField('auth-pass2', 'Повторите пароль', password2, setPassword2, 'new-password')}
            </>
          ) : null}

          {mode === 'twofa' ? (
            <>
              <label className="auth__field" htmlFor="auth-code">
                <span>{recovery ? 'Код восстановления' : 'Код подтверждения'}</span>
                {recovery ? (
                  <input
                    id="auth-code"
                    className="auth__code"
                    value={code}
                    onChange={(event) =>
                      setCode(
                        event.target.value
                          .toLowerCase()
                          .replace(/[^a-z0-9-]/g, '')
                          .slice(0, 11),
                      )
                    }
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    placeholder="abcde-fghjk"
                    required
                    autoFocus
                  />
                ) : (
                  <input
                    id="auth-code"
                    className="auth__code"
                    value={code}
                    onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="000000"
                    required
                    autoFocus
                  />
                )}
              </label>
              <button
                type="button"
                className="auth__link auth__forgot"
                onClick={() => {
                  setRecovery((value) => !value)
                  setCode('')
                }}
              >
                {recovery ? 'Ввести код из приложения' : 'Нет доступа к приложению? Код восстановления'}
              </button>
            </>
          ) : null}

          {mode === 'login' ? (
            <button type="button" className="auth__link auth__forgot" onClick={() => switchMode('forgot')}>
              Забыли пароль?
            </button>
          ) : null}

          {error ? (
            <p className="auth__error" role="alert">
              {error}
            </p>
          ) : null}
          {info ? (
            <p className="auth__info" role="status">
              {info}
            </p>
          ) : null}

          <button type="submit" className="auth__submit" disabled={busy}>
            {busy
              ? 'Подождите…'
              : { login: 'Войти', register: 'Зарегистрироваться', forgot: 'Получить ссылку', twofa: 'Подтвердить', reset: 'Сохранить и войти' }[mode]}
          </button>

          <p className="auth__small">
            Вход по логину и паролю, через Яндекс ID или с двухфакторной аутентификацией. Пароли хранятся только в виде
            хеша на сервере приложения.
          </p>

          <div className="auth__links">
            {mode === 'login' ? (
              <button type="button" className="auth__link" onClick={() => switchMode('register')}>
                Зарегистрироваться
              </button>
            ) : (
              <button type="button" className="auth__link" onClick={() => switchMode('login')}>
                Уже есть аккаунт? Войти
              </button>
            )}
            <span aria-hidden="true">·</span>
            <button type="button" className="auth__link" onClick={onGuest}>
              Продолжить без входа
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
