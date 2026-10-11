import { useEffect, useMemo, useState } from 'react'
import type { Faculty, University } from '../api/guide'
import { PinIcon, SearchIcon } from '../components/GuideIcons'
import { award } from '../lib/badges'
import { useGuide } from '../lib/session'

const EMPTY = 'нет на сайте'

function Value({ value }: { value: string | number | null | undefined }) {
  if (value === null || value === undefined || String(value).trim() === '')
    return (
      <span className="fac__empty" title="На официальном сайте вуза этого не нашли — уточните на месте">
        {EMPTY}
      </span>
    )
  return <>{value}</>
}

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function FacultyCard({ faculty, university, go }: { faculty: Faculty; university: University; go: (to: string) => void }) {
  const building = university.buildings.find((item) => item.id === faculty.building_id)
  return (
    <li className="fac card">
      <div className="fac__head">
        <h3>{faculty.name}</h3>
        {faculty.kind ? <span className="tag tag--none">{faculty.kind}</span> : null}
      </div>
      <dl className="fac__grid">
        <dt>Корпус</dt>
        <dd>
          {building ? (
            <>
              {building.name}
              {building.address ? <span className="muted"> · {building.address}</span> : null}
            </>
          ) : (
            <Value value={null} />
          )}
        </dd>
        <dt>Этаж</dt>
        <dd>
          <Value value={faculty.floor} />
        </dd>
        <dt>Кабинет деканата</dt>
        <dd>
          <Value value={faculty.room} />
        </dd>
        <dt>Часы работы</dt>
        <dd>
          <Value value={faculty.hours} />
        </dd>
        <dt>Телефон</dt>
        <dd>{faculty.phone ? <a href={`tel:${faculty.phone.replace(/[^\d+]/g, '')}`}>{faculty.phone}</a> : <Value value={null} />}</dd>
        <dt>Email</dt>
        <dd>{faculty.email ? <a href={`mailto:${faculty.email}`}>{faculty.email}</a> : <Value value={null} />}</dd>
        {faculty.dean ? (
          <>
            <dt>Руководитель</dt>
            <dd>{faculty.dean}</dd>
          </>
        ) : null}
      </dl>
      {faculty.note ? <p className="fac__note">{faculty.note}</p> : null}
      <div className="fac__foot">
        <button
          type="button"
          className="btn"
          disabled={!building?.coords}
          title={building?.coords ? undefined : 'Корпус пока не отмечен на карте'}
          onClick={() => go(`/map?u=${university.id}&b=${building?.id ?? ''}`)}
        >
          <PinIcon /> Показать на карте
        </button>
        <span className="fac__src">
          Источник:{' '}
          {(faculty.sources?.length ? faculty.sources : faculty.page ? [faculty.page] : []).slice(0, 2).map((url, index) => (
            <a key={url} href={url} target="_blank" rel="noreferrer">
              {index ? ', ' : ''}
              {host(url)}
            </a>
          ))}
        </span>
      </div>
    </li>
  )
}

/** Справка: факультеты и корпуса выбранного вуза, есть поиск и фильтр по корпусу. */
export default function Faculties({ go }: { go: (to: string) => void }) {
  const { university } = useGuide()
  const [query, setQuery] = useState('')
  const [building, setBuilding] = useState('')
  useEffect(() => award('faculties'), [])

  const items = useMemo(() => {
    if (!university) return []
    const needle = query.trim().toLowerCase().replace(/ё/g, 'е')
    return university.faculties.filter((faculty) => {
      if (building && faculty.building_id !== building) return false
      if (!needle) return true
      const hay = `${faculty.name} ${faculty.kind ?? ''} ${faculty.room ?? ''}`.toLowerCase().replace(/ё/g, 'е')
      return needle.split(/\s+/).every((word) => hay.includes(word))
    })
  }, [building, query, university])

  if (!university) return <p className="guide-empty">Загружаем данные вуза…</p>

  const usedBuildings = university.buildings.filter((item) => university.faculties.some((f) => f.building_id === item.id))

  return (
    <section className="guide-page" aria-labelledby="fac-title">
      <div className="guide-page__head">
        <p className="eyebrow">{university.short_name}</p>
        <h1 id="fac-title">Факультеты и корпуса</h1>
        <p className="muted">
          {university.data_note ??
            'Данные собраны вручную с официального сайта вуза. Если поле пустое, на сайте этих сведений нет — уточните на посту охраны или в деканате.'}
        </p>
      </div>

      <div className="fac-tools">
        <label className="search">
          <SearchIcon />
          <span className="sr-only">Поиск факультета</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Например: экономика, юридический, 2-312"
          />
        </label>
        <label className="sr-only" htmlFor="fac-building">
          Корпус
        </label>
        <select id="fac-building" className="sp" value={building} onChange={(event) => setBuilding(event.target.value)}>
          <option value="">Все корпуса</option>
          {usedBuildings.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <span className="muted">
          {items.length} из {university.faculties.length}
        </span>
      </div>

      {university.faculties.length === 0 ? (
        <p className="guide-empty">Для этого вуза справка пока не заполнена.</p>
      ) : items.length === 0 ? (
        <p className="guide-empty">Ничего не нашли. Попробуйте другое слово или сбросьте фильтр корпуса.</p>
      ) : (
        <ul className="fac-list">
          {items.map((faculty) => (
            <FacultyCard key={faculty.id} faculty={faculty} university={university} go={go} />
          ))}
        </ul>
      )}
    </section>
  )
}
