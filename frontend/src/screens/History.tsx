import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import type { HistoryItem } from '../api/guide'
import { guideApi } from '../api/guide'
import { useGuide } from '../lib/session'

type Filter = 'all' | 'miss' | 'ok'
const FILTERS: [Filter, string][] = [
  ['all', 'Все'],
  ['miss', 'С пропусками'],
  ['ok', 'Полные'],
]

const DATE = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'long',
  hour: '2-digit',
  minute: '2-digit',
})

/** История обращений того, кто вошёл: что проверяли и чем всё закончилось. */
export default function History({ go }: { go: (to: string) => void }) {
  const { user, universities } = useGuide()
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')

  async function clear() {
    try {
      await guideApi.clearHistory()
      setItems([])
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Не удалось очистить историю.')
    } finally {
      setConfirmClear(false)
    }
  }

  useEffect(() => {
    if (!user) return
    guideApi
      .history()
      .then(setItems)
      .catch((reason) => setError(reason instanceof ApiError ? reason.message : 'Не удалось загрузить историю.'))
  }, [user])

  const shortName = (id: string | null) => universities?.find((item) => item.id === id)?.short_name ?? ''

  return (
    <section className="guide-page" aria-labelledby="hist-title">
      <div className="guide-page__head">
        <h1 id="hist-title">История обращений</h1>
        <p className="muted">Храним только имя файла, тип документа и итог проверки. Сами документы не сохраняются.</p>
      </div>
      {!user ? (
        <div className="guide-empty card">
          <p>История ведётся для вошедших пользователей.</p>
          <button type="button" className="btn btn--primary" onClick={() => go('/login')}>
            Войти
          </button>
        </div>
      ) : error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : items === null ? (
        <p className="guide-empty">Загружаем…</p>
      ) : items.length === 0 ? (
        <div className="guide-empty card">
          <p>Обращений пока нет.</p>
          <button type="button" className="btn btn--primary" onClick={() => go('/dean')}>
            Проверить документ
          </button>
        </div>
      ) : (
        <>
          <div className="seg hist__filter" role="group" aria-label="Показать">
            {FILTERS.map(([key, label]) => {
              const count =
                key === 'all'
                  ? items.length
                  : items.filter((item) => (key === 'ok' ? item.complete : !item.complete)).length
              return (
                <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                  {label} · {count}
                </button>
              )
            })}
          </div>
          <ul className="hist-list">
            {items
              .filter((item) => (filter === 'all' ? true : filter === 'ok' ? item.complete : !item.complete))
              .map((item) => (
                <li key={item.id} className="hist card">
                  <span className={item.complete ? 'stamp-dot ok' : 'stamp-dot miss'} aria-hidden="true" />
                  <div>
                    <b>{item.document_type_title || item.document_type || 'Документ'}</b>
                    <span className="muted">
                      {DATE.format(new Date(item.created_at))}
                      {item.filename ? ` · ${item.filename}` : ''}
                      {item.university_id ? ` · ${shortName(item.university_id)}` : ''}
                    </span>
                    {item.complete === false && item.missing.length ? (
                      <span className="hist__miss">
                        Не хватало: {(item.missing_labels?.length ? item.missing_labels : item.missing).join(', ')}
                      </span>
                    ) : null}
                  </div>
                  <span className={item.complete ? 'tag tag--ok' : 'tag tag--miss'}>
                    {item.complete ? 'все сведения есть' : 'есть пропуски'}
                  </span>
                </li>
              ))}
          </ul>
        </>
      )}
      {user && items && items.length ? (
        <div className="hist__tools">
          {confirmClear ? (
            <>
              <span>Удалить все записи истории? Отменить это нельзя.</span>
              <button type="button" className="btn btn--danger" onClick={clear}>
                Да, очистить
              </button>
              <button type="button" className="btn btn--quiet" onClick={() => setConfirmClear(false)}>
                Отмена
              </button>
            </>
          ) : (
            <button type="button" className="btn btn--quiet" onClick={() => setConfirmClear(true)}>
              Очистить историю
            </button>
          )}
        </div>
      ) : null}
    </section>
  )
}
