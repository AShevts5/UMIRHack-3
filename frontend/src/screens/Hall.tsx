import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { award } from '../lib/badges'
import type { SectionState } from '../api/guide'
import { openAssistant } from '../components/Assistant'
import { DeanIcon, FacultyIcon, HistoryIcon, MapIcon, ShieldIcon } from '../components/GuideIcons'
import { useGuide } from '../lib/session'

interface Tile {
  key: 'dean' | 'faculties' | 'map' | 'history'
  route: string
  title: string
  text: string
  icon: ReactNode
}

const TILES: Tile[] = [
  {
    key: 'dean',
    route: '/dean',
    title: 'Обращение в деканат',
    text: 'Загрузите справку или заявление: проверим обязательные сведения по Положению.',
    icon: <DeanIcon />,
  },
  {
    key: 'faculties',
    route: '/faculties',
    title: 'Факультеты и корпуса',
    text: 'Где деканат, этаж и кабинет, часы работы и контакты — с официального сайта.',
    icon: <FacultyIcon />,
  },
  {
    key: 'map',
    route: '/map',
    title: 'Карта вузов',
    text: 'Корпуса на карте и выбор своего вуза.',
    icon: <MapIcon />,
  },
  {
    key: 'history',
    route: '/history',
    title: 'История обращений',
    text: 'Ваши прошлые проверки документов и их итог.',
    icon: <HistoryIcon />,
  },
]

const STATE_LABEL: Record<SectionState, string | null> = { available: null, soon: 'скоро', off: 'недоступно' }

/** Холл вуза: фон и тексты тянем из конфига вуза, а сверху — кнопки разделов. */
export default function Hall({ go }: { go: (to: string) => void }) {
  const { university, user } = useGuide()
  const hallImage = university?.hall?.hall_image ?? '/universities/_default/hall.svg'
  const sections = university?.sections
  useEffect(() => award('enter'), [])

  return (
    <section className="hall" aria-labelledby="hall-title">
      <div className="hall__bg" style={{ backgroundImage: `url("${hallImage}")` }} role="img" aria-label={university?.hall?.hall_caption ?? 'Холл главного корпуса'} />
      <div className="hall__in">
        <div className="hall__head">
          <p className="eyebrow">{university?.short_name ?? 'Вуз'} · {university?.city ?? ''}</p>
          <h1 id="hall-title">{university?.hall?.title ?? 'Вы в холле'}</h1>
          <p>
            {user?.name ? `${user.name}, ` : ''}
            {university?.hall?.welcome ?? 'выберите, что нужно сделать.'}
          </p>
          <button type="button" className="hall__guard" onClick={() => openAssistant()}>
            <ShieldIcon /> Спросить на посту охраны
          </button>
        </div>
        <div className="hall__grid">
          {TILES.map((tile) => {
            const state: SectionState = sections?.[tile.key] ?? 'available'
            if (state === 'off') return null
            const badge = STATE_LABEL[state]
            const disabled = state !== 'available'
            return (
              <button
                key={tile.key}
                type="button"
                className={`hall__tile hall__tile--${tile.key}`}
                onClick={() => go(tile.route)}
                disabled={disabled}
                aria-describedby={`tile-${tile.key}`}
              >
                <span className="hall__icon">{tile.icon}</span>
                <b>
                  {tile.title}
                  {badge ? <span className="tag tag--none">{badge}</span> : null}
                </b>
                <span id={`tile-${tile.key}`}>
                  {tile.key === 'dean' && disabled
                    ? 'Проверка документов появится, когда добавим положение этого вуза.'
                    : tile.text}
                </span>
              </button>
            )
          })}
        </div>
        {university?.regulation_note && sections?.dean === 'available' ? <p className="hall__note">{university.regulation_note}</p> : null}
      </div>
    </section>
  )
}
