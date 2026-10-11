interface Props {
  text: string
  /** Подпись выбранного поля: строку с ней подсвечиваем. */
  highlight: string | null
  meanConfidence: number
  wordCount: number
  expert: boolean
  engine: string
  languages: string
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Распознанный текст как есть, без причёсывания; строка выбранного поля подсвечена. */
export default function OcrText({ text, highlight, meanConfidence, wordCount, expert, engine, languages }: Props) {
  const term = highlight?.trim() ?? ''
  const pattern = term ? new RegExp(`${escapeRegExp(term)}[^\\n]*`, 'i') : null
  const match = pattern ? pattern.exec(text) : null

  return (
    <div>
      <pre className="ocr" tabIndex={0} aria-label="Распознанный текст документа">
        {match ? (
          <>
            {text.slice(0, match.index)}
            <mark>{match[0]}</mark>
            {text.slice(match.index + match[0].length)}
          </>
        ) : (
          text || 'Текст не распознан.'
        )}
      </pre>
      <p className="hint">
        Распознано на этом компьютере · средняя уверенность {meanConfidence.toFixed(1)}% · текст не сохраняется
        {expert ? ` · ${engine} ${languages} · ${wordCount} слов с координатами` : ''}
      </p>
    </div>
  )
}
