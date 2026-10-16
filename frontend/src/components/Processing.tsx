import { useEffect, useState } from 'react'
import { FileIcon } from './Icons'

const STEPS: { title: string; timings: string[] }[] = [
  { title: 'Чтение файла и подготовка страницы', timings: ['load', 'preprocess'] },
  { title: 'Распознавание текста', timings: ['ocr'] },
  { title: 'Определение типа и сбор полей', timings: ['classify', 'fields'] },
  { title: 'Поиск подписи и печати по графике', timings: ['marks'] },
  { title: 'Сверка с Положением', timings: ['rules', 'validation'] },
]

interface Props {
  fileName: string
  thumbUrl: string | null
  /** Появляется, когда API ответил: тогда все этапы честно помечаем выполненными. */
  timings: Record<string, number> | null
}

/**
 * Экран «думаем». Пока API считает, этапы щёлкают по таймеру, но последний не
 * закрываем: финальную галочку ставим только после ответа, без обмана.
 */
export default function Processing({ fileName, thumbUrl, timings }: Props) {
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (timings) return
    const id = window.setInterval(() => setStep((value) => Math.min(value + 1, STEPS.length - 1)), 650)
    return () => window.clearInterval(id)
  }, [timings])

  // Секундомер: на CPU распознавание идёт несколько секунд, и без него кажется,
  // что всё зависло. После ответа показываем, сколько вышло в итоге.
  const [started] = useState(() => performance.now())
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (timings) return
    const id = window.setInterval(() => setElapsed(performance.now() - started), 100)
    return () => window.clearInterval(id)
  }, [started, timings])
  const total = timings ? Object.values(timings).reduce((sum, value) => sum + value, 0) : elapsed

  const current = timings ? STEPS.length : step

  return (
    <div className="proc card" aria-live="polite">
      {thumbUrl ? (
        <div className="thumb" style={{ backgroundImage: `url(${thumbUrl})` }} />
      ) : (
        <div className="thumb thumb--file">
          <FileIcon />
        </div>
      )}
      <div>
        <span className="eyebrow mono" style={{ textTransform: 'none', letterSpacing: 0 }}>
          {fileName}
        </span>
        <h2 style={{ fontSize: 'var(--t-xl)', marginTop: 4 }}>Проверяем документ</h2>
        <ol className="steps">
          {STEPS.map((item, index) => {
            const state = index < current ? 'is-done' : index === current ? 'is-run' : ''
            const ms = timings
              ? item.timings.reduce((sum, key) => sum + (timings[key] ?? 0), 0)
              : 0
            return (
              <li key={item.title} className={state}>
                <i aria-hidden="true">{index < current ? '✓' : ''}</i>
                {item.title}
                {timings && ms > 0 ? <span className="time">{Math.round(ms)} мс</span> : null}
                <span className="sr-only">{index < current ? ' — готово' : index === current ? ' — выполняется' : ''}</span>
              </li>
            )
          })}
        </ol>
        <p className="clock" aria-hidden="true">
          {timings ? 'Готово за' : 'Прошло'}{' '}
          {(total / 1000).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} с
        </p>
      </div>
    </div>
  )
}
