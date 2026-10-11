/**
 * Геймификация: значки за всякие действия в путеводителе. Живут только в этом браузере
 * (localStorage) — на сервер ничего не шлём, в профиле так честно и написано.
 */
export interface Badge {
  id: BadgeId
  title: string
  text: string
  /** подсказка, как получить, пока значка ещё нет */
  hint: string
  glyph: string
}

export type BadgeId = 'enter' | 'first-check' | 'complete-doc' | 'faculties' | 'runner' | 'ask' | 'voice' | 'secure'

export const BADGES: Badge[] = [
  {
    id: 'enter',
    title: 'Добро пожаловать',
    text: 'Вошли в холл главного корпуса',
    hint: 'Войдите в холл главного корпуса',
    glyph: '🚪',
  },
  {
    id: 'first-check',
    title: 'Первое обращение',
    text: 'Проверили первый документ',
    hint: 'Проверьте первый документ',
    glyph: '📄',
  },
  {
    id: 'complete-doc',
    title: 'Полный комплект',
    text: 'Документ, в котором есть все обязательные сведения',
    hint: 'Проверьте документ, в котором есть все сведения',
    glyph: '✅',
  },
  {
    id: 'faculties',
    title: 'Знаю, где деканат',
    text: 'Открыли справку по факультетам',
    hint: 'Откройте справку по факультетам',
    glyph: '🏛️',
  },
  {
    id: 'runner',
    title: 'До корпуса бегом',
    text: 'Добежали до корпуса на карте',
    hint: 'Добегите до корпуса на карте',
    glyph: '🏃',
  },
  {
    id: 'ask',
    title: 'Вопрос на посту',
    text: 'Задали вопрос помощнику',
    hint: 'Задайте вопрос помощнику',
    glyph: '💬',
  },
  {
    id: 'voice',
    title: 'Голосом',
    text: 'Спросили помощника голосом',
    hint: 'Спросите помощника голосом',
    glyph: '🎙️',
  },
  {
    id: 'secure',
    title: 'Под защитой',
    text: 'Включили двухфакторную аутентификацию',
    hint: 'Включите двухфакторную аутентификацию',
    glyph: '🛡️',
  },
]

const KEY = 'umir-badges'
export const BADGE_EVENT = 'umir:badge'

export function earnedBadges(): Record<string, string> {
  try {
    const raw = localStorage.getItem(KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

/** Выдаём значок; если он новый — пинаем интерфейс (вылезет плашка). */
export function award(id: BadgeId) {
  const earned = earnedBadges()
  if (earned[id]) return
  earned[id] = new Date().toISOString()
  try {
    localStorage.setItem(KEY, JSON.stringify(earned))
  } catch {
    return
  }
  const badge = BADGES.find((item) => item.id === id)
  // С задержкой: значок могут выдать в эффекте экрана раньше, чем плашка успела подписаться. Костыль, но работает.
  if (badge) window.setTimeout(() => window.dispatchEvent(new CustomEvent<Badge>(BADGE_EVENT, { detail: badge })), 60)
}
