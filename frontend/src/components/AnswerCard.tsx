import type { Answer } from '../api/types'

interface Props {
  answer: Answer
  question: string
}

export default function AnswerCard({ answer, question }: Props) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="section-title" style={{ marginBottom: 8 }}>
        <span>Ответ на вопрос</span>
        <span className="badge">{answer.answerable ? 'ответ по документу' : 'сведений нет'}</span>
      </div>
      <p className="card__hint" style={{ marginBottom: 8 }}>
        {question}
      </p>
      <div className="answer">
        <p className="answer__text">{answer.answer}</p>
        <p className="answer__value">
          answerable: <strong>{String(answer.answerable)}</strong> · value:{' '}
          <strong>{answer.value === null ? 'null' : answer.value}</strong> · sources:{' '}
          <strong>{answer.sources.length ? answer.sources.join(', ') : '[]'}</strong>
        </p>
      </div>
      <p className="disclaimer">
        Ссылки показывают источник утверждения: <code>field:&lt;ключ&gt;</code> — значение из документа,{' '}
        <code>rule:N.M</code> — пункт Положения. Пустой список источников допустим только когда ответа нет.
      </p>
    </div>
  )
}
