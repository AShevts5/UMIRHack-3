import { useEffect, useRef, useState } from 'react'
import type { Answer } from '../api/types'
import type { RequirementItem } from '../lib/format'
import { RuleChip } from './RulePopover'

export interface QaEntry {
  id: number
  question: string
  answer: Answer | null
  error?: string
}

interface Props {
  entries: QaEntry[]
  suggestions: string[]
  items: RequirementItem[]
  expert: boolean
  busy: boolean
  onAsk: (question: string) => void
  onShowField: (key: string) => void
}

const MAX = 500

/** Вкладка «Вопросы»: подсказки и лента вопрос-ответ со ссылками на источники. */
export default function AnswerCard({ entries, suggestions, items, expert, busy, onAsk, onShowField }: Props) {
  const [draft, setDraft] = useState('')
  const threadRef = useRef<HTMLDivElement | null>(null)
  const lastAnswered = entries.filter((entry) => entry.answer || entry.error).length

  // пришёл новый вопрос или ответ — крутим к нему, а не бросаем где-то за краем экрана
  useEffect(() => {
    if (entries.length < 2) return
    const smooth = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    threadRef.current?.lastElementChild?.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: smooth })
  }, [entries.length, lastAnswered])

  const submit = (question: string) => {
    const text = question.trim()
    if (!text || busy) return
    onAsk(text)
    setDraft('')
  }

  return (
    <div className="chat">
      <div className="chips">
        {suggestions.map((sample) => (
          <button type="button" key={sample} className="chip" disabled={busy} onClick={() => submit(sample)}>
            {sample}
          </button>
        ))}
      </div>

      <div className="thread" aria-live="polite" ref={threadRef}>
        {entries.map((entry) => (
          <div key={entry.id} style={{ display: 'contents' }}>
            <div className="q">{entry.question}</div>
            {entry.error ? (
              <div className="a is-error">
                <p>{entry.error}</p>
              </div>
            ) : entry.answer ? (
              <AnswerBubble answer={entry.answer} items={items} expert={expert} onShowField={onShowField} />
            ) : (
              <div className="a is-pending">
                <p className="muted">Ищем ответ в документе и Положении…</p>
              </div>
            )}
          </div>
        ))}
      </div>

      <form
        className="ask"
        onSubmit={(event) => {
          event.preventDefault()
          submit(draft)
        }}
      >
        <label htmlFor="ask-input" className="sr-only">
          Вопрос по документу
        </label>
        <input
          id="ask-input"
          value={draft}
          maxLength={MAX}
          autoComplete="off"
          placeholder="Например: какая дата выдачи?"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="btn btn--primary" disabled={busy || !draft.trim()}>
          Спросить
        </button>
      </form>
    </div>
  )
}

function AnswerBubble({
  answer,
  items,
  expert,
  onShowField,
}: {
  answer: Answer
  items: RequirementItem[]
  expert: boolean
  onShowField: (key: string) => void
}) {
  return (
    <div className={answer.answerable ? 'a' : 'a is-none'}>
      <div className="a__kind">
        {answer.answerable ? (
          <span className="tag tag--ok">ответ по источникам</span>
        ) : (
          <span className="tag tag--none">сведений нет</span>
        )}
      </div>
      <p>{answer.answer}</p>
      {answer.sources.length ? (
        <div className="a__src">
          Источник:{' '}
          {answer.sources.map((source) => {
            if (source.startsWith('rule:')) return <RuleChip key={source} rule={source} />
            const key = source.replace(/^field:/, '')
            const label = items.find((item) => item.key === key)?.label ?? key
            return (
              <button type="button" key={source} className="rule src-field" onClick={() => onShowField(key)}>
                {label}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="a__src">
          Сервис не дополняет ответ догадками <RuleChip rule="2.4" />
        </div>
      )}
      {expert ? (
        <div className="a__raw" style={{ display: 'block' }}>
          answerable: {String(answer.answerable)} · value: {answer.value === null ? 'null' : JSON.stringify(answer.value)} ·
          sources: [{answer.sources.join(', ')}]
        </div>
      ) : null}
    </div>
  )
}
