import type { MarkView, FieldView } from '../api/types'

export interface Highlight {
  box: { x: number; y: number; width: number; height: number } | null
  label: string
  kind: 'field' | 'mark'
}

interface Props {
  fieldViews: FieldView[]
  markViews: MarkView[]
  activeKey: string | null
  onSelect: (box: Highlight['box'], label: string) => void
  onSelectMark: (box: Highlight['box'], label: string) => void
}

const NOT_FOUND = 'не найдено'

export default function FieldCard({ fieldViews, markViews, activeKey, onSelect, onSelectMark }: Props) {
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div>
        <div className="section-title" style={{ marginBottom: 8 }}>
          <span>Обязательные текстовые поля</span>
          <span className="badge">
            найдено {fieldViews.filter((field) => field.found).length} из {fieldViews.length}
          </span>
        </div>
        <div className="fields">
          {fieldViews.map((field) => {
            const missing = !field.found || field.value === NOT_FOUND
            return (
              <button
                key={field.key}
                type="button"
                className={[
                  'field-item',
                  missing ? 'field-item--missing' : '',
                  activeKey === field.label ? 'field-item--active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={() => onSelect(field.box, field.label)}
                title={field.box ? 'Показать место на изображении' : 'Координаты не найдены'}
              >
                <p className="field-item__label">{field.label}</p>
                <p className={missing ? 'field-item__value field-item__value--missing' : 'field-item__value'}>
                  {missing ? 'не найдено' : String(field.value)}
                </p>
                <p className="field-item__meta">
                  {field.key} · {field.rule} · уверенность {Math.round(field.confidence * 100)}%
                </p>
              </button>
            )
          })}
        </div>
      </div>

      <div>
        <div className="section-title" style={{ marginBottom: 8 }}>
          <span>Графические отметки</span>
          <span className="badge">анализ пикселей, не надписей</span>
        </div>
        <div className="marks">
          {markViews.map((mark) => (
            <button
              key={mark.key}
              type="button"
              className={mark.present ? 'mark mark--present' : 'mark mark--absent'}
              onClick={() => onSelectMark(mark.box, mark.label)}
              title={mark.box ? 'Показать найденную зону' : undefined}
            >
              <span className="status__icon" aria-hidden>
                {mark.present ? '✓' : '—'}
              </span>
              <span style={{ textAlign: 'left' }}>
                <span className="mark__title">{mark.label}</span>
                <span className="mark__meta" style={{ display: 'block' }}>
                  {mark.present ? 'обнаружена графика' : 'графика не обнаружена'} · метод{' '}
                  {mark.method || 'n/a'} · {Math.round(mark.confidence * 100)}%
                </span>
              </span>
            </button>
          ))}
        </div>
        <p className="disclaimer">
          Надпись «Подпись» или «М.П.» сама по себе не считается отметкой: проверяется наличие графического элемента
          (пункт 2.3 Положения). Подлинность не проверяется.
        </p>
      </div>
    </div>
  )
}
