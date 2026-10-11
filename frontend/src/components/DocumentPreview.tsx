import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { RequirementItem } from '../lib/format'

export interface StageHandle {
  /** Докрутить лист до зоны элемента. */
  reveal: (key: string, scrollPage: boolean) => void
}

interface Props {
  fileName: string
  imageUrl: string | null
  /** Размер картинки, в координатах которой пришли рамки (0 — берём из самой картинки). */
  width: number
  height: number
  items: RequirementItem[]
  activeKey: string | null
  onSelect: (key: string) => void
  /** Исходный снимок, если страницу подравнивали: тогда можно сравнить «до» и «после». */
  originalUrl?: string | null
}

const PAD = 4
const MIN_ZOOM = 0.6
const MAX_ZOOM = 2.5

/**
 * Лист документа с пронумерованными зонами. Номер булавки = номер строки в
 * требованиях. Подпись и печать видны всегда, текстовые поля — когда выбрали или по
 * кнопке «Показать все зоны». Рамки считаем в процентах от размера страницы,
 * поэтому они не съезжают при любом масштабе.
 */
const DocumentPreview = forwardRef<StageHandle, Props>(function DocumentPreview(
  { fileName, imageUrl: processedUrl, width, height, items: allItems, activeKey, onSelect, originalUrl },
  ref,
) {
  const [original, setOriginal] = useState(false)
  useEffect(() => setOriginal(false), [processedUrl])
  const showOriginal = Boolean(original && originalUrl)
  const imageUrl = showOriginal ? originalUrl! : processedUrl
  // Рамки считали по выровненной странице, так что на исходнике их не рисуем — съедут.
  const items = showOriginal ? [] : allItems
  const [zoom, setZoom] = useState(1)
  const [showAll, setShowAll] = useState(false)
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setNatural(null)
    setZoom(1)
    stageRef.current?.scrollTo({ top: 0 })
  }, [imageUrl])

  useImperativeHandle(ref, () => ({
    reveal(key, scrollPage) {
      const stage = stageRef.current
      const zone = stage?.querySelector<HTMLElement>(`[data-zone="${CSS.escape(key)}"]`)
      if (!stage) return
      const smooth = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
      if (zone) {
        const zr = zone.getBoundingClientRect()
        const sr = stage.getBoundingClientRect()
        stage.scrollTo({ top: stage.scrollTop + zr.top - sr.top - sr.height / 2 + zr.height / 2, behavior: smooth })
      }
      if (scrollPage && window.innerWidth <= 1020) stage.scrollIntoView({ behavior: smooth, block: 'nearest' })
    },
  }))

  const baseW = width || natural?.width || 0
  const baseH = height || natural?.height || 0

  return (
    <div className="viewer">
      <div className="vbar">
        <span className="name" title={fileName}>
          {fileName}
        </span>
        {originalUrl ? (
          <div className="seg vbar__seg" role="group" aria-label="Какой снимок показать">
            <button type="button" aria-pressed={!showOriginal} onClick={() => setOriginal(false)}>
              После выравнивания
            </button>
            <button type="button" aria-pressed={showOriginal} onClick={() => setOriginal(true)}>
              Исходник
            </button>
          </div>
        ) : null}
        <button
          type="button"
          className="btn btn--quiet"
          aria-pressed={showAll}
          disabled={showOriginal}
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? 'Скрыть зоны' : 'Показать все зоны'}
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label="Уменьшить"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => setZoom((v) => Math.max(MIN_ZOOM, v - 0.25))}
        >
          −
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label="Увеличить"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => setZoom((v) => Math.min(MAX_ZOOM, v + 0.25))}
        >
          +
        </button>
      </div>

      <div
        className="stage"
        ref={stageRef}
        // без рамок (исходник) в листе нет кнопок: фокус даём самой области, чтобы крутить её с клавиатуры
        tabIndex={items.length ? undefined : 0}
        role={items.length ? undefined : 'region'}
        aria-label={items.length ? undefined : 'Исходный снимок документа'}
      >
        <div className={showAll ? 'page show-all' : 'page'} style={{ width: `${zoom * 100}%` }}>
          {imageUrl ? (
            <img
              src={imageUrl}
              alt="Страница загруженного документа"
              onLoad={(event) =>
                setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
              }
            />
          ) : (
            <div className="no-image">Превью этой страницы недоступно, но текст и поля распознаны.</div>
          )}
          {imageUrl && baseW > 0 && baseH > 0
            ? items.map((item) => {
                if (!item.box || item.box.width <= 0 || item.box.height <= 0) return null
                const { x, y, width: w, height: h } = item.box
                const cls = [
                  'zone',
                  item.kind === 'mark' ? 'm' : 'f',
                  item.found ? '' : 'miss',
                  activeKey === item.key ? 'is-active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
                const name = `${item.n}. ${item.label}${item.found ? '' : ' — не найдено'}`
                return (
                  <button
                    key={item.key}
                    type="button"
                    className={cls}
                    data-zone={item.key}
                    title={name}
                    aria-label={name}
                    aria-pressed={activeKey === item.key}
                    onClick={() => onSelect(item.key)}
                    style={{
                      left: `${((x - PAD) / baseW) * 100}%`,
                      top: `${((y - PAD) / baseH) * 100}%`,
                      width: `${((w + 2 * PAD) / baseW) * 100}%`,
                      height: `${((h + 2 * PAD) / baseH) * 100}%`,
                    }}
                  >
                    <span className="pin" aria-hidden="true">
                      {item.n}
                    </span>
                  </button>
                )
              })
            : null}
        </div>
      </div>

      <div className="legend">
        <span>
          <i style={{ borderColor: 'var(--accent)', background: 'var(--hl)' }} />
          текстовое поле
        </span>
        <span>
          <i style={{ borderColor: 'var(--mark)', background: 'var(--mark-soft)' }} />
          подпись или печать
        </span>
        <span>
          <i style={{ borderColor: 'var(--miss)', borderStyle: 'dashed' }} />
          ожидаемое место, элемента нет
        </span>
        <span className="mono">{Math.round(zoom * 100)}%</span>
      </div>
    </div>
  )
})

export default DocumentPreview
