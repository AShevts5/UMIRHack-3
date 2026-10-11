import { useRef, useState } from 'react'
import type { DocumentTypesResponse } from '../api/types'

interface Props {
  file: File | null
  catalog: DocumentTypesResponse | null
  documentType: string
  question: string
  busy: boolean
  previewUrl: string | null
  onFileChange: (file: File | null) => void
  onDocumentTypeChange: (value: string) => void
  onQuestionChange: (value: string) => void
  onAnalyze: () => void
  onCheckOnly: () => void
}

const ACCEPT = '.png,.jpg,.jpeg,.pdf'

export default function UploadPanel(props: Props) {
  const {
    file,
    catalog,
    documentType,
    question,
    busy,
    previewUrl,
    onFileChange,
    onDocumentTypeChange,
    onQuestionChange,
    onAnalyze,
    onCheckOnly,
  } = props
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [dragActive, setDragActive] = useState(false)

  const maxMb = catalog ? Math.round(catalog.limits.max_upload_bytes / (1024 * 1024)) : 12

  return (
    <section className="card">
      <h2 className="card__title">Документ студента</h2>
      <p className="card__hint">
        PNG, JPG или одностраничный PDF до {maxMb} МБ. Файл обрабатывается локально и не покидает компьютер.
      </p>

      <div
        className={dragActive ? 'dropzone dropzone--active' : 'dropzone'}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragActive(false)
          const dropped = event.dataTransfer.files?.[0]
          if (dropped) onFileChange(dropped)
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click()
        }}
      >
        <p className="dropzone__title">{file ? file.name : 'Перетащите файл сюда или нажмите для выбора'}</p>
        <p className="dropzone__hint">
          {file ? `${(file.size / 1024).toFixed(0)} КБ · ${file.type || 'тип не указан'}` : 'Справка об обучении, заявление на академический отпуск, справка о практике'}
        </p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
      />

      {previewUrl ? (
        <div className="preview" style={{ marginTop: 14, maxHeight: 220, overflow: 'hidden' }}>
          <img src={previewUrl} alt="Предпросмотр загруженного документа" />
        </div>
      ) : null}

      <div className="field">
        <label className="field__label" htmlFor="document-type">
          Тип документа
        </label>
        <select
          id="document-type"
          value={documentType}
          onChange={(event) => onDocumentTypeChange(event.target.value)}
        >
          <option value="auto">Определить автоматически (бонус)</option>
          {catalog?.document_types.map((type) => (
            <option key={type.code} value={type.code}>
              {type.title}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label" htmlFor="question">
          Вопрос по документу
        </label>
        <textarea
          id="question"
          value={question}
          maxLength={500}
          onChange={(event) => onQuestionChange(event.target.value)}
          placeholder="Например: Укажи значение поля «Дата выдачи»."
        />
        <div className="chip-row">
          {[
            catalog?.default_question ?? 'Содержит ли документ все обязательные сведения?',
            'Кто принимает окончательное решение о принятии документа?',
            'Какова стоимость обучения за следующий семестр?',
          ].map((sample) => (
            <button
              type="button"
              key={sample}
              className={question === sample ? 'chip chip--active' : 'chip'}
              onClick={() => onQuestionChange(sample)}
            >
              {sample.length > 46 ? `${sample.slice(0, 44)}…` : sample}
            </button>
          ))}
        </div>
      </div>

      <div className="actions">
        <button className="button--primary" onClick={onAnalyze} disabled={busy || !file}>
          {busy ? 'Обработка…' : 'Задать вопрос'}
        </button>
        <button className="button--ghost" onClick={onCheckOnly} disabled={busy || !file}>
          Проверить документ
        </button>
        {file ? (
          <button className="button--ghost" onClick={() => onFileChange(null)} disabled={busy}>
            Сбросить
          </button>
        ) : null}
      </div>
    </section>
  )
}
