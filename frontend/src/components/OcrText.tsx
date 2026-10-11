import type { WordBox } from '../api/types'

interface Props {
  text: string
  boxes: WordBox[]
  highlight: string | null
}

/**
 * Shows the raw OCR output.  When a field is selected in the card, its label is
 * highlighted inside the text so the reader can verify the reading directly.
 */
export default function OcrText({ text, boxes, highlight }: Props) {
  const term = highlight?.trim().toLowerCase() ?? ''
  const lines = text.split('\n')

  return (
    <div>
      <pre className="ocr">
        {lines.map((line, index) => {
          const lower = line.toLowerCase()
          const position = term ? lower.indexOf(term) : -1
          if (position < 0) {
            return (
              <span key={index}>
                {line}
                {'\n'}
              </span>
            )
          }
          return (
            <span key={index}>
              {line.slice(0, position)}
              <mark className="hit">{line.slice(position, position + term.length)}</mark>
              {line.slice(position + term.length)}
              {'\n'}
            </span>
          )
        })}
      </pre>
      <p className="disclaimer">
        Распознано локально ({boxes.length} слов с координатами). Текст не отправлялся во внешние сервисы и не
        сохраняется на диск.
      </p>
    </div>
  )
}
