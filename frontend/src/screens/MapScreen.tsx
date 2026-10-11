import { award } from '../lib/badges'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from 'react-leaflet'
import type { Building, UniversitySummary } from '../api/guide'
import { SearchIcon } from '../components/GuideIcons'
import { useGuide } from '../lib/session'

const ROSTOV: [number, number] = [47.2313, 39.7233]

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch] ?? ch)
}

function universityIcon(item: UniversitySummary, active: boolean) {
  const logo = item.logo ? `<img src="${escapeHtml(item.logo)}" alt="" />` : `<b>${escapeHtml(item.short_name.slice(0, 4))}</b>`
  return L.divIcon({
    className: '',
    html: `<span class="map-pin${active ? ' is-active' : ''}">${logo}</span>`,
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -22],
  })
}

const buildingIcon = (active: boolean) =>
  L.divIcon({
    className: '',
    html: `<span class="map-bld${active ? ' is-active' : ''}"></span>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    popupAnchor: [0, -10],
  })

function FlyTo({ target, zoom }: { target: [number, number] | null; zoom: number }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.flyTo(target, zoom, { duration: 0.6 })
  }, [map, target, zoom])
  return null
}

type LatLon = [number, number]

/** Расстояние по прямой в метрах (формула гаверсинусов, привет матану). */
function distance(a: LatLon, b: LatLon): number {
  const rad = Math.PI / 180
  const dLat = (b[0] - a[0]) * rad
  const dLon = (b[1] - a[1]) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2
  return 2 * 6371000 * Math.asin(Math.sqrt(h))
}

function formatDistance(m: number): string {
  return m < 1000 ? `${Math.round(m / 10) * 10} м` : `${(m / 1000).toFixed(1).replace('.', ',')} км`
}

/** ~4,5 км/ч пешком; считаем по прямой, поэтому честно пишем «примерно». */
function walkMinutes(m: number): number {
  return Math.max(1, Math.round(m / 75))
}

// Человечек: стоит или бежит (CSS-анимацию ног вырубаем при prefers-reduced-motion).
const runnerIcon = (running: boolean) =>
  L.divIcon({
    className: '',
    html: `<span class="runner${running ? ' is-running' : ''}" aria-hidden="true"><svg viewBox="0 0 32 40" width="32" height="40"><circle cx="18" cy="6" r="4.5" fill="#B0441B"/><path class="runner__body" d="M17 11l-3 11 6 5 1 10M14 22l-6 6M16 14l7 4 4-3M16 14l-7 2-3 5" fill="none" stroke="#B0441B" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`,
    iconSize: [32, 40],
    iconAnchor: [16, 38],
  })

const SECTION_TEXT = { available: 'проверка документов', soon: 'проверка документов — скоро', off: '' } as const

/** Карта вузов: метки вузов, корпуса выбранного вуза и карточка «Перейти в вуз». */
export default function MapScreen({ go, params }: { go: (to: string) => void; params: URLSearchParams }) {
  const { universities, university, universityId, chooseUniversity } = useGuide()
  const [query, setQuery] = useState('')
  const focusUniversity = params.get('u') || universityId
  const focusBuilding = params.get('b') || ''
  const [target, setTarget] = useState<{ at: [number, number]; zoom: number } | null>(null)

  const list = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (universities ?? []).filter(
      (item) => !needle || `${item.name} ${item.short_name} ${item.city ?? ''}`.toLowerCase().includes(needle),
    )
  }, [query, universities])

  const shown = (universities ?? []).find((item) => item.id === focusUniversity) ?? null
  const buildings: Building[] = (shown?.buildings ?? []).filter((item): item is Building & { coords: [number, number] } => !!item.coords)

  useEffect(() => {
    const building = buildings.find((item) => item.id === focusBuilding)
    if (building?.coords) setTarget({ at: building.coords, zoom: 17 })
    else if (shown) setTarget({ at: shown.coords, zoom: 14 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusBuilding, shown?.id])

  // ---- геймификация: «я здесь» и прогулочка до корпуса
  const [me, setMe] = useState<LatLon | null>(null)
  const [meNote, setMeNote] = useState<string | null>(null)
  const [walker, setWalker] = useState<LatLon | null>(null)
  const [walkTo, setWalkTo] = useState<Building | null>(null)
  const [arrived, setArrived] = useState<Building | null>(null)
  const frame = useRef<number | null>(null)

  useEffect(() => () => {
    if (frame.current) cancelAnimationFrame(frame.current)
  }, [])

  const place = (point: LatLon, note: string) => {
    if (frame.current) cancelAnimationFrame(frame.current)
    setMe(point)
    setWalker(point)
    setWalkTo(null)
    setArrived(null)
    setMeNote(note)
    setTarget({ at: point, zoom: 15 })
  }

  const locate = () => {
    if (!('geolocation' in navigator)) {
      setMeNote('Браузер не даёт определить место. Начните от входа в вуз.')
      return
    }
    setMeNote('Определяем, где вы…')
    navigator.geolocation.getCurrentPosition(
      (position) => place([position.coords.latitude, position.coords.longitude], 'Вы здесь (по геолокации браузера).'),
      () => setMeNote('Не удалось определить место: доступ запрещён или нет сигнала. Начните от входа в вуз.'),
      { enableHighAccuracy: true, timeout: 10000 },
    )
  }

  const demoStart = () => {
    if (!shown) return
    // точка в паре кварталов от главного корпуса — на случай демо без геолокации
    place([shown.coords[0] - 0.0035, shown.coords[1] - 0.0045], 'Демо-старт в паре кварталов от главного корпуса.')
  }

  const nearest = useMemo(() => {
    if (!me) return []
    return buildings
      .map((item) => ({ item, meters: distance(me, item.coords as LatLon) }))
      .sort((a, b) => a.meters - b.meters)
      .slice(0, 3)
  }, [buildings, me])

  const walk = (building: Building) => {
    const from = walker ?? me
    if (!from || !building.coords) return
    if (frame.current) cancelAnimationFrame(frame.current)
    const to = building.coords as LatLon
    setWalkTo(building)
    setArrived(null)
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const duration = reduced ? 0 : Math.min(4500, 1200 + distance(from, to) / 2)
    const started = performance.now()
    const step = (now: number) => {
      const t = duration ? Math.min(1, (now - started) / duration) : 1
      const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      setWalker([from[0] + (to[0] - from[0]) * ease, from[1] + (to[1] - from[1]) * ease])
      if (t < 1) frame.current = requestAnimationFrame(step)
      else {
        frame.current = null
        setArrived(building)
        setMe(to)
        award('runner')
      }
    }
    frame.current = requestAnimationFrame(step)
    setTarget({ at: to, zoom: 16 })
  }

  const enter = async (id: string) => {
    await chooseUniversity(id)
    go('/hall')
  }

  return (
    <section className="map-page" aria-labelledby="map-title">
      <aside className="map-side card">
        <h1 id="map-title">Карта вузов</h1>
        <p className="muted">Выберите свой вуз — откроется его холл. Пока в реестре два вуза Ростова-на-Дону.</p>
        <label className="search">
          <SearchIcon />
          <span className="sr-only">Поиск вуза</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Название или город" />
        </label>
        <ul className="map-list">
          {list.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={item.id === focusUniversity ? 'map-item is-active' : 'map-item'}
                onClick={() => go(`/map?u=${item.id}`)}
              >
                {item.logo ? <img src={item.logo} alt="" width={36} height={36} /> : null}
                <span>
                  <b>{item.short_name}</b>
                  <small>
                    {item.city} · {item.buildings.length} корп.
                    {item.sections.dean === 'soon' ? ' · проверка скоро' : ''}
                  </small>
                </span>
              </button>
            </li>
          ))}
          {list.length === 0 ? <li className="muted">Такого вуза пока нет в реестре.</li> : null}
        </ul>
        {shown ? (
          <div className="walk">
            <p className="eyebrow">Дойти до корпуса</p>
            <div className="walk__start">
              <button type="button" className="btn" onClick={locate}>
                Где я?
              </button>
              <button type="button" className="btn btn--quiet" onClick={demoStart}>
                Старт рядом с вузом
              </button>
            </div>
            {meNote ? <p className="muted walk__note">{meNote}</p> : null}
            {arrived ? (
              <div className="walk__done" role="status">
                <b>Вы у корпуса «{arrived.name}»</b>
                <button type="button" className="btn btn--primary" onClick={() => enter(shown.id)}>
                  Войти в холл
                </button>
              </div>
            ) : null}
            {nearest.length && !walkTo ? (
              <ul className="walk__list">
                {nearest.map(({ item, meters }) => (
                  <li key={item.id}>
                    <span>
                      <b>{item.name}</b>
                      <small>
                        {formatDistance(meters)} по прямой · ~{walkMinutes(meters)} мин пешком
                      </small>
                    </span>
                    <button type="button" className="btn" onClick={() => walk(item)}>
                      Идти
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {walkTo && !arrived ? <p className="muted walk__note">Бежим к корпусу «{walkTo.name}»…</p> : null}
            {arrived ? (
              <button type="button" className="btn btn--quiet" onClick={() => {
                  setWalkTo(null)
                  setArrived(null)
                }}>
                Выбрать другой корпус
              </button>
            ) : null}
          </div>
        ) : null}
        {shown ? (
          <div className="map-current">
            <p className="eyebrow">Корпуса · {shown.short_name}</p>
            <ul>
              {shown.buildings.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={item.id === focusBuilding ? 'is-active' : undefined}
                    disabled={!item.coords}
                    onClick={() => go(`/map?u=${shown.id}&b=${item.id}`)}
                  >
                    {item.name}
                    {item.address ? <small>{item.address}</small> : null}
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" className="btn btn--primary" onClick={() => enter(shown.id)}>
              {shown.id === university?.id ? 'Вернуться в холл' : 'Перейти в вуз'}
            </button>
          </div>
        ) : null}
      </aside>

      <div className="map-box card">
        <MapContainer center={shown?.coords ?? ROSTOV} zoom={13} scrollWheelZoom className="map">
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FlyTo target={target?.at ?? null} zoom={target?.zoom ?? 13} />
          {(universities ?? []).map((item) => (
            <Marker key={item.id} position={item.coords} icon={universityIcon(item, item.id === focusUniversity)} title={item.name} alt={item.name}>
              <Popup>
                <div className="map-pop">
                  <b>{item.name}</b>
                  <span>
                    {item.city}
                    {SECTION_TEXT[item.sections.dean] ? ` · ${SECTION_TEXT[item.sections.dean]}` : ''}
                  </span>
                  <button type="button" className="btn btn--primary" onClick={() => enter(item.id)}>
                    Перейти в вуз
                  </button>
                </div>
              </Popup>
            </Marker>
          ))}
          {buildings.map((item) => (
            <Marker
              key={`${shown?.id}-${item.id}`}
              position={item.coords as [number, number]}
              icon={buildingIcon(item.id === focusBuilding)}
              title={item.name}
              alt={item.name}
            >
              <Popup>
                <div className="map-pop">
                  <b>{item.name}</b>
                  {item.address ? <span>{item.address}</span> : null}
                  {item.sources[0] ? (
                    <a href={item.sources[0]} target="_blank" rel="noreferrer">
                      источник
                    </a>
                  ) : null}
                </div>
              </Popup>
            </Marker>
          ))}
          {walker && walkTo && !arrived && walkTo.coords ? (
            <Polyline positions={[walker, walkTo.coords as LatLon]} pathOptions={{ color: '#B0441B', weight: 3, dashArray: '6 8' }} />
          ) : null}
          {walker ? <Marker position={walker} icon={runnerIcon(Boolean(walkTo && !arrived))} zIndexOffset={1000} interactive={false} keyboard={false} /> : null}
        </MapContainer>
      </div>
    </section>
  )
}
