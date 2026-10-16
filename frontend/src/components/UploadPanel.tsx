import { useEffect, useRef, useState } from 'react'
import type { DocumentTypesResponse } from '../api/types'
import { plural } from '../lib/format'
import { AlertIcon, LockIcon, UploadIcon } from './Icons'
import { RuleChip } from './RulePopover'

export interface StartError {
  title: string
  text?: string
  action?: { label: string; run: () => void }
}

interface Props {
  catalog: DocumentTypesResponse | null
  /** Почему сейчас загрузить не выйдет (OCR или сервис лежит). */
  blocked: string | null
  error: StartError | null
  onFile: (file: File) => void
}

const ACCEPT = '.png,.jpg,.jpeg,.pdf'

/** Образцы для кнопок «Пример»: собираются скриптом backend/scripts/make_demo_samples.py. */
export const DEMO_SAMPLES = [
  { file: 'study-certificate.png', title: 'Справка об обучении', note: 'все сведения на месте' },
  { file: 'academic-leave.png', title: 'Заявление на отпуск', note: 'нет подписи и даты' },
  { file: 'internship-certificate.pdf', title: 'Справка о практике', note: 'PDF' },
  { file: 'photo-angle.jpg', title: 'Фото под углом', note: 'тень и поворот' },
] as const

const MIME: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', pdf: 'application/pdf' }

/** Достаём картинку из буфера обмена (скриншот, «Копировать изображение») как файл. */
export function fileFromClipboard(data: DataTransfer | null): File | null {
  if (!data) return null
  const direct = Array.from(data.files ?? []).find((item) => /^image\/(png|jpeg)$|^application\/pdf$/.test(item.type))
  const item =
    direct ??
    Array.from(data.items ?? [])
      .find((entry) => /^image\/(png|jpeg)$/.test(entry.type))
      ?.getAsFile()
  if (!item) return null
  if (item.name && /\.(png|jpe?g|pdf)$/i.test(item.name)) return item
  const ext = item.type === 'image/jpeg' ? 'jpg' : item.type === 'application/pdf' ? 'pdf' : 'png'
  return new File([item], `вставка-из-буфера.${ext}`, { type: item.type })
}

async function loadSample(name: string): Promise<File> {
  const response = await fetch(`${import.meta.env.BASE_URL}demo/${name}`)
  if (!response.ok) throw new Error(String(response.status))
  const ext = name.slice(name.lastIndexOf('.') + 1)
  return new File([await response.blob()], name, { type: MIME[ext] ?? 'application/octet-stream' })
}

/** Стартовый экран: dropzone и что конкретно проверяем для каждого типа. */
export default function UploadPanel({ catalog, blocked, error, onFile }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const cameraRef = useRef<HTMLInputElement | null>(null)
  const [over, setOver] = useState(false)
  const [sampleError, setSampleError] = useState(false)
  const [touch] = useState(() => window.matchMedia?.('(pointer: coarse)').matches ?? false)

  // Ctrl+V где угодно на стартовом экране: прилетает скриншот или скопированная картинка.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (blocked) return
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      const pasted = fileFromClipboard(event.clipboardData)
      if (pasted) {
        event.preventDefault()
        onFile(pasted)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [blocked, onFile])

  const trySample = async (name: string) => {
    if (blocked) return
    setSampleError(false)
    try {
      onFile(await loadSample(name))
    } catch {
      setSampleError(true)
    }
  }
  const maxMb = catalog ? Math.round(catalog.limits.max_upload_bytes / (1024 * 1024)) : 12

  const choose = () => {
    if (!blocked) inputRef.current?.click()
  }

  return (
    <div className="start">
      <div className="intake">
        <span className="eyebrow">Предварительная проверка</span>
        <h1>Проверьте документ до визита в деканат</h1>
        <p>
          Загрузите справку или заявление. Сервис прочитает текст, найдёт обязательные поля, подпись и печать и сверит
          их с Положением. Ничего не отправляется в интернет.
        </p>

        <div
          className={['drop', over ? 'is-over' : '', blocked ? 'is-disabled' : ''].filter(Boolean).join(' ')}
          role="button"
          tabIndex={0}
          aria-disabled={Boolean(blocked)}
          aria-describedby="drop-hint"
          onClick={choose}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              choose()
            }
          }}
          onDragOver={(event) => {
            event.preventDefault()
            if (!blocked) setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => {
            event.preventDefault()
            setOver(false)
            const dropped = event.dataTransfer.files?.[0]
            if (dropped && !blocked) onFile(dropped)
          }}
        >
          <UploadIcon />
          <b>Перетащите файл документа</b>
          <span id="drop-hint">
            {blocked ?? `PNG, JPG или одностраничный PDF, до ${maxMb} МБ`}
            {blocked || touch ? null : <> · или вставьте снимок: Ctrl+V</>}
          </span>
          <span className="btn btn--primary" aria-hidden="true">
            Выбрать файл
          </span>
        </div>
        {touch ? (
          <button
            type="button"
            className="btn drop__camera"
            disabled={Boolean(blocked)}
            onClick={() => cameraRef.current?.click()}
          >
            Сфотографировать документ
          </button>
        ) : null}
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          hidden
          onChange={(event) => {
            const picked = event.target.files?.[0]
            event.target.value = ''
            if (picked) onFile(picked)
          }}
        />

        <div className="demo-samples" role="group" aria-labelledby="samples-title">
          <span className="demo-samples__title" id="samples-title">
            Нет документа под рукой? Попробуйте пример:
          </span>
          <div className="demo-samples__list">
            {DEMO_SAMPLES.map((sample, index) => (
              <button
                key={sample.file}
                type="button"
                className="demo-sample"
                disabled={Boolean(blocked)}
                onClick={() => trySample(sample.file)}
              >
                <b>
                  Пример {index + 1}. {sample.title}
                </b>
                <span>{sample.note}</span>
              </button>
            ))}
          </div>
          {sampleError ? (
            <p className="demo-samples__error" role="alert">
              Не удалось загрузить пример. Обновите страницу и попробуйте ещё раз.
            </p>
          ) : null}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(event) => {
            const picked = event.target.files?.[0]
            event.target.value = ''
            if (picked) onFile(picked)
          }}
        />

        {error ? (
          <div className="alert" role="alert">
            <AlertIcon />
            <div>
              <p>
                <b>{error.title}</b>
              </p>
              {error.text ? <p>{error.text}</p> : null}
              {error.action ? (
                <button type="button" className="btn" onClick={error.action.run}>
                  {error.action.label}
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <aside className="types">
        <span className="eyebrow">Что проверяется</span>
        <div style={{ display: 'grid', gap: 12 }}>
          {catalog ? (
            catalog.document_types.map((type) => {
              const textRule = type.fields[0]?.rule ?? ''
              return (
                <div className="card type" key={type.code}>
                  <h3>{type.title}</h3>
                  <ul>
                    <li>
                      <span>
                        {type.fields.length}{' '}
                        {plural(type.fields.length, 'текстовое поле', 'текстовых поля', 'текстовых полей')}
                      </span>
                      <RuleChip rule={textRule} />
                    </li>
                    {type.marks.map((mark) => (
                      <li key={mark.key}>
                        <span>{mark.label}</span>
                        <RuleChip rule={mark.rule} />
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })
          ) : (
            <div className="card type">
              <p className="type__empty">Список типов появится, когда сервис проверки ответит.</p>
            </div>
          )}
        </div>
        <p className="note">
          <LockIcon />
          <span>
            Результат предварительный. Окончательное решение принимает сотрудник деканата <RuleChip rule="1.3" />
          </span>
        </p>
      </aside>
    </div>
  )
}
