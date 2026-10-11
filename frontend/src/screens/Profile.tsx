import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../api/client'
import { guideApi } from '../api/guide'
import { ShieldIcon, YandexIcon } from '../components/GuideIcons'
import { BADGES, award, earnedBadges } from '../lib/badges'
import { useGuide } from '../lib/session'

/** Профиль: имя, вуз, Яндекс ID, двухфакторка, пароль, мои данные и выход. */
export default function Profile({ go, params }: { go: (to: string) => void; params?: URLSearchParams }) {
  const { user, setUser, signOut, universities, universityId, chooseUniversity, config } = useGuide()
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null)
  const [code, setCode] = useState('')
  const [disabling, setDisabling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [pw, setPw] = useState({ current: '', next: '', repeat: '' })
  const [pwBusy, setPwBusy] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwInfo, setPwInfo] = useState<string | null>(null)
  const [name, setName] = useState(user?.name ?? '')
  const [nameInfo, setNameInfo] = useState<string | null>(null)
  const [yaError, setYaError] = useState<string | null>(params?.get('error') ?? null)
  const [yaInfo, setYaInfo] = useState<string | null>(params?.get('linked') ? 'Яндекс ID привязан: теперь можно входить и через него.' : null)
  const [deleting, setDeleting] = useState(false)
  const [renewing, setRenewing] = useState(false)
  const [codes, setCodes] = useState<string[] | null>(null)
  const [removal, setRemoval] = useState({ password: '', code: '' })
  const [dataError, setDataError] = useState<string | null>(null)

  useEffect(() => setName(user?.name ?? ''), [user?.name])

  // вернулись из Яндекс ID после привязки: перетянем профиль с сервера
  const linked = params?.get('linked')
  useEffect(() => {
    if (linked) guideApi.me().then(setUser).catch(() => undefined)
  }, [linked, setUser])

  if (!user) {
    return (
      <section className="guide-page">
        <div className="guide-empty card">
          <p>Войдите, чтобы открыть профиль.</p>
          <button type="button" className="btn btn--primary" onClick={() => go('/login')}>
            Войти
          </button>
        </div>
      </section>
    )
  }

  const fail = (reason: unknown) => setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.')

  async function startSetup() {
    setError(null)
    setInfo(null)
    try {
      setSetup(await guideApi.totpSetup())
    } catch (reason) {
      fail(reason)
    }
  }

  async function confirm() {
    setError(null)
    try {
      if (disabling) {
        setUser(await guideApi.totpDisable(code))
        setDisabling(false)
        setCodes(null)
        setInfo('Двухфакторная аутентификация выключена.')
      } else if (renewing) {
        const result = await guideApi.totpRecovery(code)
        setUser(result.user)
        setCodes(result.recovery_codes)
        setRenewing(false)
        setInfo('Новые коды восстановления готовы, старые больше не действуют.')
      } else {
        const result = await guideApi.totpEnable(code)
        setUser(result.user)
        setCodes(result.recovery_codes)
        award('secure')
        setSetup(null)
        setInfo('Готово: теперь при входе понадобится код из приложения.')
      }
      setCode('')
    } catch (reason) {
      fail(reason)
    }
  }

  function downloadCodes(list: string[]) {
    const text = [
      'Коды восстановления «Онлайн-деканата»',
      `Аккаунт: ${user?.login ?? user?.email ?? ''}`,
      'Каждый код срабатывает один раз. Храните их отдельно от телефона.',
      '',
      ...list,
      '',
    ].join('\n')
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    link.download = 'umir-recovery-codes.txt'
    link.click()
    setTimeout(() => URL.revokeObjectURL(link.href), 1000)
  }

  // Код из приложения: 6 цифр. Когда выключаем 2FA, прокатит и код восстановления (вдруг телефон потеряли).
  const codeOk = /^\d{6}$/.test(code) || (disabling && /^[a-z0-9]{5}-?[a-z0-9]{5}$/.test(code) && !/^\d+$/.test(code))

  async function changePassword(event: FormEvent) {
    event.preventDefault()
    setPwError(null)
    setPwInfo(null)
    if (pw.next !== pw.repeat) {
      setPwError('Пароли не совпадают.')
      return
    }
    setPwBusy(true)
    try {
      const hadPassword = user?.has_password
      setUser(await guideApi.changePassword(pw.next, hadPassword ? pw.current : undefined))
      setPw({ current: '', next: '', repeat: '' })
      setPwInfo(hadPassword ? 'Пароль изменён.' : 'Пароль задан: теперь можно входить и по логину.')
    } catch (reason) {
      setPwError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.')
    } finally {
      setPwBusy(false)
    }
  }

  const failText = (reason: unknown, fallback = 'Сервер не ответил.') => (reason instanceof ApiError ? reason.message : fallback)

  async function saveName(event: FormEvent) {
    event.preventDefault()
    setNameInfo(null)
    try {
      setUser(await guideApi.updateMe({ name }))
      setNameInfo('Сохранено.')
    } catch (reason) {
      setNameInfo(failText(reason))
    }
  }

  async function linkYandex() {
    setYaError(null)
    setYaInfo(null)
    try {
      window.location.href = await guideApi.yandexLink()
    } catch (reason) {
      setYaError(failText(reason))
    }
  }

  async function unlinkYandex() {
    setYaError(null)
    setYaInfo(null)
    try {
      setUser(await guideApi.yandexUnlink())
      setYaInfo('Яндекс ID отвязан.')
    } catch (reason) {
      setYaError(failText(reason))
    }
  }

  async function download() {
    setDataError(null)
    try {
      const data = await guideApi.exportData()
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'umir-my-data.json'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) {
      setDataError(failText(reason))
    }
  }

  async function removeAccount(event: FormEvent) {
    event.preventDefault()
    setDataError(null)
    try {
      await guideApi.deleteAccount(removal.password || undefined, removal.code || undefined)
      signOut()
      go('/login')
    } catch (reason) {
      setDataError(failText(reason))
    }
  }

  const yandexReady = config?.auth.yandex ?? false
  const earned = earnedBadges()
  const earnedCount = BADGES.filter((badge) => earned[badge.id]).length

  return (
    <section className="guide-page profile" aria-labelledby="prof-title">
      <div className="guide-page__head">
        <h1 id="prof-title">{user.name ?? 'Профиль'}</h1>
        <p className="muted">
          {[user.login, user.email].filter(Boolean).join(' · ')}
          {user.yandex_linked ? (
            <span className="profile__ya">
              <YandexIcon size={16} /> Яндекс ID привязан
            </span>
          ) : null}
        </p>
      </div>

      <form className="card profile__block" onSubmit={saveName}>
        <h2>Как к вам обращаться</h2>
        <div className="profile__row">
          <input
            className="profile__input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            aria-label="Имя"
            placeholder="Имя"
          />
          <button type="submit" className="btn" disabled={name === (user.name ?? '')}>
            Сохранить
          </button>
        </div>
        {nameInfo ? <p className="muted" role="status">{nameInfo}</p> : null}
      </form>

      <div className="card profile__block">
        <h2>Мой вуз</h2>
        <select className="sp" aria-label="Мой вуз" value={universityId} onChange={(event) => chooseUniversity(event.target.value)}>
          {(universities ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.short_name} — {item.city}
            </option>
          ))}
        </select>
      </div>

      {user.demo ? (
        <div className="card profile__block">
          <h2>
            <ShieldIcon /> Демо-аккаунт
          </h2>
          <p className="muted">
            Это общий аккаунт для жюри и гостей: в нём можно проверять документы, смотреть историю и значки. Пароль,
            вход через Яндекс, двухфакторная аутентификация и удаление здесь выключены, чтобы аккаунт работал для всех.
          </p>
        </div>
      ) : (
        <>
        <div className="card profile__block">
          <h2>
            <YandexIcon size={18} /> Яндекс ID
          </h2>
          {user.yandex_linked ? (
            <>
              <p className="muted">Привязан: можно входить кнопкой «Войти через Яндекс».</p>
              <button type="button" className="btn" onClick={unlinkYandex} disabled={!user.has_password}>
                Отвязать
              </button>
              {!user.has_password ? <p className="muted">Чтобы отвязать, сначала задайте пароль ниже, иначе войти будет нельзя.</p> : null}
            </>
          ) : yandexReady ? (
            <>
              <p className="muted">Привяжите Яндекс ID, чтобы входить в один клик без пароля.</p>
              <button type="button" className="btn btn--primary" onClick={linkYandex}>
                Привязать Яндекс ID
              </button>
            </>
          ) : (
            <p className="muted">Вход через Яндекс на этом сервере не настроен: администратор не задал ключи приложения.</p>
          )}
          {yaError ? (
            <p className="auth__error" role="alert">
              {yaError}
            </p>
          ) : null}
          {yaInfo ? (
            <p className="auth__info" role="status">
              {yaInfo}
            </p>
          ) : null}
        </div>

        <div className="card profile__block">
          <h2>
            <ShieldIcon /> Двухфакторная аутентификация
          </h2>
          <p className="muted">
            {user.two_factor_enabled
              ? 'Включена: при входе после пароля нужен шестизначный код из приложения-аутентификатора.'
              : 'Код из приложения-аутентификатора (Яндекс Ключ, Google Authenticator и т.п.) защитит вход, даже если пароль узнают.'}
          </p>

          {setup ? (
            <div className="totp">
              <div className="totp__qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} aria-label="QR-код для приложения-аутентификатора" />
              <div>
                <p>Отсканируйте QR-код или введите ключ вручную:</p>
                <code className="totp__secret">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</code>
              </div>
            </div>
          ) : null}

          {codes ? (
            <div className="recovery" role="region" aria-label="Коды восстановления">
              <p>
                <b>Сохраните коды восстановления.</b> Если телефон потеряется, любой из них заменит код из приложения при
                входе. Каждый код срабатывает один раз, и больше мы их не покажем.
              </p>
              <ol className="recovery__list">
                {codes.map((item) => (
                  <li key={item}>
                    <code>{item}</code>
                  </li>
                ))}
              </ol>
              <div className="profile__row">
                <button type="button" className="btn" onClick={() => downloadCodes(codes)}>
                  Скачать .txt
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => navigator.clipboard?.writeText(codes.join('\n')).catch(() => undefined)}
                >
                  Скопировать
                </button>
                <button type="button" className="btn btn--primary" onClick={() => setCodes(null)}>
                  Я сохранил коды
                </button>
              </div>
            </div>
          ) : null}

          {setup || disabling || renewing ? (
            <div className="totp__confirm">
              <input
                value={code}
                onChange={(event) =>
                  setCode(
                    disabling
                      ? event.target.value
                          .toLowerCase()
                          .replace(/[^a-z0-9-]/g, '')
                          .slice(0, 11)
                      : event.target.value.replace(/\D/g, '').slice(0, 6),
                  )
                }
                inputMode={disabling ? 'text' : 'numeric'}
                autoComplete="one-time-code"
                placeholder={disabling ? '000000 или код восстановления' : '000000'}
                aria-label={disabling ? 'Код из приложения или код восстановления' : 'Код из приложения'}
              />
              <button type="button" className="btn btn--primary" onClick={confirm} disabled={!codeOk}>
                {disabling ? 'Выключить' : renewing ? 'Получить новые коды' : 'Включить'}
              </button>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => {
                  setSetup(null)
                  setDisabling(false)
                  setRenewing(false)
                  setCode('')
                }}
              >
                Отмена
              </button>
            </div>
          ) : user.two_factor_enabled ? (
            <>
              <p className="muted">Кодов восстановления осталось: {user.recovery_codes_left ?? 0} из 10.</p>
              <div className="profile__row">
                <button type="button" className="btn" onClick={() => setRenewing(true)}>
                  Новые коды восстановления
                </button>
                <button type="button" className="btn" onClick={() => setDisabling(true)}>
                  Выключить 2FA
                </button>
              </div>
            </>
          ) : (
            <button type="button" className="btn btn--primary" onClick={startSetup}>
              Включить 2FA
            </button>
          )}
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
        </div>

        <form className="card profile__block profile__pw" onSubmit={changePassword}>
          <h2>{user.has_password ? 'Сменить пароль' : 'Задать пароль'}</h2>
          {!user.has_password ? (
            <p className="muted">Сейчас вы входите только через Яндекс ID. С паролем можно будет входить и по логину.</p>
          ) : null}
          {user.has_password ? (
            <label className="profile__field">
              <span>Текущий пароль</span>
              <input
                type="password"
                value={pw.current}
                onChange={(event) => setPw({ ...pw, current: event.target.value })}
                autoComplete="current-password"
                required
              />
            </label>
          ) : null}
          <label className="profile__field">
            <span>Новый пароль (от 8 символов)</span>
            <input
              type="password"
              value={pw.next}
              onChange={(event) => setPw({ ...pw, next: event.target.value })}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <label className="profile__field">
            <span>Повторите новый пароль</span>
            <input
              type="password"
              value={pw.repeat}
              onChange={(event) => setPw({ ...pw, repeat: event.target.value })}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <button type="submit" className="btn btn--primary" disabled={pwBusy}>
            {pwBusy ? 'Сохраняю…' : 'Сохранить пароль'}
          </button>
          {pwError ? (
            <p className="auth__error" role="alert">
              {pwError}
            </p>
          ) : null}
          {pwInfo ? (
            <p className="auth__info" role="status">
              {pwInfo}
            </p>
          ) : null}
        </form>
        </>
      )}

      <div className="card profile__block">
        <h2>
          Значки <span className="muted profile__count">{earnedCount} из {BADGES.length}</span>
        </h2>
        <ul className="badges">
          {BADGES.map((badge) => (
            <li key={badge.id} className={earned[badge.id] ? 'badge is-on' : 'badge'} title={earned[badge.id] ? badge.text : badge.hint}>
              <span className="badge__glyph" aria-hidden="true">
                {badge.glyph}
              </span>
              <b>{badge.title}</b>
              <small>{earned[badge.id] ? badge.text : badge.hint}</small>
            </li>
          ))}
        </ul>
        <p className="muted">Значки хранятся только в этом браузере и никуда не отправляются.</p>
      </div>

      <div className="card profile__block">
        <h2>Мои данные</h2>
        <p className="muted">
          Приложение хранит профиль и историю обращений (имя файла, тип документа, итог проверки). Сами документы не
          сохраняются.
        </p>
        <div className="profile__row">
          <button type="button" className="btn" onClick={download}>
            Скачать мои данные
          </button>
          {!deleting && !user.demo ? (
            <button type="button" className="btn btn--danger-quiet" onClick={() => setDeleting(true)}>
              Удалить аккаунт
            </button>
          ) : null}
        </div>
        {deleting ? (
          <form className="profile__danger" onSubmit={removeAccount}>
            <p>Аккаунт и вся история обращений будут удалены без возможности восстановления.</p>
            {user.has_password ? (
              <label className="profile__field">
                <span>Пароль</span>
                <input
                  type="password"
                  value={removal.password}
                  onChange={(event) => setRemoval({ ...removal, password: event.target.value })}
                  autoComplete="current-password"
                  autoFocus
                  required
                />
              </label>
            ) : null}
            {user.two_factor_enabled ? (
              <label className="profile__field">
                <span>Код из приложения-аутентификатора</span>
                <input
                  value={removal.code}
                  onChange={(event) => setRemoval({ ...removal, code: event.target.value.replace(/\D/g, '').slice(0, 6) })}
                  autoFocus={!user.has_password}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                />
              </label>
            ) : null}
            <div className="profile__row">
              <button type="submit" className="btn btn--danger">
                Удалить навсегда
              </button>
              <button type="button" className="btn btn--quiet" onClick={() => setDeleting(false)}>
                Отмена
              </button>
            </div>
          </form>
        ) : null}
        {dataError ? (
          <p className="auth__error" role="alert">
            {dataError}
          </p>
        ) : null}
      </div>

      <button
        type="button"
        className="btn profile__out"
        onClick={() => {
          signOut()
          go('/login')
        }}
      >
        Выйти
      </button>
    </section>
  )
}
