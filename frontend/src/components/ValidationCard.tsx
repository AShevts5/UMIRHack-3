import type { AnalysisResponse } from '../api/types'
import type { RequirementItem } from '../lib/format'
import { plural } from '../lib/format'
import { BangMark, CheckMark } from './Icons'
import { RuleChip } from './RulePopover'

interface Props {
  result: AnalysisResponse
  items: RequirementItem[]
  onAsk: () => void
  onFix: () => void
}

/** Вердикт: штамп, одна строчка про то, чего не хватает, основание и оговорка п. 1.3. */
export default function ValidationCard({ result, items, onAsk, onFix }: Props) {
  const ok = result.validation.status === 'document_ok'
  const missing = result.validation.missing_fields.map(
    (key) => items.find((item) => item.key === key)?.label ?? key,
  )
  const count = missing.length
  const marks = items.filter((item) => item.kind === 'mark').map((item) => item.label.toLowerCase())

  return (
    <section className={ok ? 'verdict verdict--ok' : 'verdict verdict--miss'} aria-labelledby="verdict-title">
      <div className="stamp" aria-hidden="true">
        {ok ? <CheckMark /> : <BangMark />}
      </div>
      <div>
        <h2 id="verdict-title">
          {ok
            ? 'Все обязательные сведения на месте'
            : `Не хватает ${count} ${plural(count, 'обязательного элемента', 'обязательных элементов', 'обязательных элементов')}`}
        </h2>
        <p>
          {ok ? (
            `Найдено ${items.length} из ${items.length}: текстовые поля${marks.length ? `, ${marks.join(' и ')}` : ''}.`
          ) : (
            <>
              Нет:{' '}
              {missing.map((label, index) => (
                <span key={label}>
                  {index > 0 ? ', ' : ''}
                  <b>{label.toLowerCase()}</b>
                </span>
              ))}
              . Остальные сведения прочитаны.
            </>
          )}
        </p>
        {result.validation.sources.length ? (
          <div className="verdict__src">
            Основание:{' '}
            {result.validation.sources.map((source) => (
              <RuleChip key={source} rule={source} />
            ))}
          </div>
        ) : null}
      </div>
      <div className="verdict__foot">
        <span className="sp">
          Это предварительная проверка, а не решение о принятии. Решение принимает сотрудник деканата{' '}
          <RuleChip rule="1.3" />
        </span>
        {ok ? null : (
          <button type="button" className="btn" onClick={onFix}>
            Загрузить исправленный
          </button>
        )}
        <button type="button" className="btn btn--primary" onClick={onAsk}>
          Задать вопрос
        </button>
      </div>
    </section>
  )
}
