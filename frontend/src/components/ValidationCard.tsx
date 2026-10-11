import type { Validation } from '../api/types'

interface Props {
  validation: Validation
  message: string
}

export default function ValidationCard({ validation, message }: Props) {
  const ok = validation.status === 'document_ok'
  return (
    <div className={ok ? 'status status--ok' : 'status status--missing'}>
      <span className="status__icon" aria-hidden>
        {ok ? '✓' : '!'}
      </span>
      <div>
        <p className="status__title">
          {ok ? 'Все обязательные сведения присутствуют' : 'Не хватает обязательных элементов'}
        </p>
        <p className="status__text">{message}</p>
        {validation.missing_fields.length > 0 ? (
          <p className="status__text" style={{ marginTop: 6 }}>
            Отсутствуют: <code>{validation.missing_fields.join(', ')}</code>
          </p>
        ) : null}
        <div className="sources">
          {validation.sources.map((source) => (
            <span className="source" key={source}>
              {source}
            </span>
          ))}
        </div>
        <p className="disclaimer">
          Это результат предварительной проверки наличия сведений, а не решение о принятии документа: окончательное
          решение принимает сотрудник деканата (пункт 1.3 Положения).
        </p>
      </div>
    </div>
  )
}
