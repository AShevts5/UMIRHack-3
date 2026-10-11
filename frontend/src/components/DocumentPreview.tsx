import { useEffect, useState } from 'react'
import type { Highlight } from './FieldCard'

interface Props {
  url: string | null
  highlight: Highlight | null
}

/**
 * Renders the uploaded page and overlays the selected zone.
 *
 * The overlay is computed from the *natural* image size reported by the browser —
 * the same pixel space as the OCR boxes returned by the backend — and is positioned
 * inside a wrapper that hugs the image, so it stays correct at any zoom level.
 */
export function DocumentPreview({ url, highlight }: Props) {
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    setNatural(null)
  }, [url])

  if (!url) {
    return <p className="skeleton">Файл ещё не загружен.</p>
  }

  const box = highlight?.box ?? null
  const showBox = Boolean(box && natural && box.width > 0 && box.height > 0)

  return (
    <div>
      <div className="preview__toolbar">
        <button className="button--ghost" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>
          −
        </button>
        <span className="badge">{Math.round(zoom * 100)}%</span>
        <button className="button--ghost" onClick={() => setZoom((value) => Math.min(3, value + 0.25))}>
          +
        </button>
        {highlight ? <span className="badge">показано: {highlight.label}</span> : null}
      </div>

      <div className="preview">
        <div className="preview__frame" style={{ width: `${zoom * 100}%` }}>
          <img
            src={url}
            alt="Страница загруженного документа"
            onLoad={(event) =>
              setNatural({
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              })
            }
          />
          {showBox && box && natural ? (
            <div
              className={highlight?.kind === 'mark' ? 'preview__box preview__box--mark' : 'preview__box'}
              style={{
                left: `${(box.x / natural.width) * 100}%`,
                top: `${(box.y / natural.height) * 100}%`,
                width: `${(box.width / natural.width) * 100}%`,
                height: `${(box.height / natural.height) * 100}%`,
              }}
            />
          ) : null}
        </div>
      </div>

      <p className="disclaimer">
        {box && natural
          ? `Зона: x=${box.x}, y=${box.y}, ширина=${box.width}, высота=${box.height} px в системе координат исходного изображения ${natural.width}×${natural.height} (масштаб отображения ${Math.round(zoom * 100)}%).`
          : 'Выберите поле или отметку в карточке, чтобы увидеть его место на странице.'}
      </p>
    </div>
  )
}

export default DocumentPreview
