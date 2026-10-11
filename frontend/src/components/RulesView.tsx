import { useEffect, useRef } from 'react'
import type { RegulationItem } from '../api/types'

interface Props {
  sections: RegulationItem[] | null
  required: Set<string>
  /** Пункт, куда прыгнули из подсказки: подсвечиваем и докручиваем до него. */
  flash: string | null
}

const anchor = (point: string) => `cl-${point.replace('.', '-')}`

/** Раздел «Положение»: весь rules.md целиком, с навигацией по разделам. */
export default function RulesView({ sections, required, flash }: Props) {
  const articleRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!flash || !sections) return
    const node = document.getElementById(anchor(flash))
    node?.scrollIntoView({ block: 'center' })
  }, [flash, sections])

  return (
    <div className="rules">
      <nav aria-label="Разделы Положения">
        {(sections ?? []).map((section) => (
          <button
            type="button"
            key={section.point}
            onClick={() => document.getElementById(`sec-${section.point}`)?.scrollIntoView({ behavior: 'smooth' })}
          >
            {section.point}. {section.title}
          </button>
        ))}
      </nav>
      <article ref={articleRef}>
        <div>
          <span className="eyebrow">Версия 1.0 · вымышленный документ кейса</span>
          <h1>Положение о предварительной проверке студенческих документов</h1>
          <p className="muted" style={{ marginTop: 8 }}>
            Единственный источник правил сервиса. Пункты с точкой — обязательные требования, на которые ссылается
            заключение.
          </p>
        </div>
        {sections === null ? <p className="skeleton">Загружаем Положение…</p> : null}
        {sections && sections.length === 0 ? (
          <p className="skeleton">Сервис не вернул текст Положения (файл rules.md не найден).</p>
        ) : null}
        {(sections ?? []).map((section) => (
          <section id={`sec-${section.point}`} key={section.point}>
            <h2>
              {section.point}. {section.title}
            </h2>
            {section.text ? <p className="muted" style={{ marginBottom: 8 }}>{section.text}</p> : null}
            {section.children.map((clause) => (
              <div
                key={clause.point}
                id={anchor(clause.point)}
                className={['clause', required.has(clause.point) ? 'req' : '', flash === clause.point ? 'is-flash' : '']
                  .filter(Boolean)
                  .join(' ')}
              >
                <b>{clause.point}</b>
                <span>{clause.text}</span>
              </div>
            ))}
          </section>
        ))}
      </article>
    </div>
  )
}
