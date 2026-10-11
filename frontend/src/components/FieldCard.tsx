import type { RequirementGroup } from '../lib/format'
import { displayValue } from '../lib/format'
import { RuleChip } from './RulePopover'

interface Props {
  groups: RequirementGroup[]
  activeKey: string | null
  expert: boolean
  onSelect: (key: string) => void
}

/** Вкладка «Требования»: строки раскиданы по группам — по пунктам Положения. */
export default function FieldCard({ groups, activeKey, expert, onSelect }: Props) {
  return (
    <div>
      {groups.map((group) => {
        const found = group.items.filter((item) => item.found).length
        const total = group.items.length
        const isMark = group.items[0]?.kind === 'mark'
        return (
          <div className="group" key={`${group.rule}-${group.title}`}>
            <div className="group__head">
              <RuleChip rule={group.rule} />
              <h3>{group.title}</h3>
              <span className="meter" aria-label={`${found} из ${total}`}>
                <i>
                  <b style={{ width: `${(found / Math.max(total, 1)) * 100}%`, ...(found < total ? { background: 'var(--miss)' } : {}) }} />
                </i>
                {found} из {total}
              </span>
            </div>
            <ul className="rows">
              {group.items.map((item) => (
                <li key={item.key}>
                  <button
                    type="button"
                    data-row={item.key}
                    className={['row', item.found ? '' : 'is-miss', activeKey === item.key ? 'is-active' : '']
                      .filter(Boolean)
                      .join(' ')}
                    aria-pressed={activeKey === item.key}
                    title={item.box ? 'Показать на листе' : 'Место на листе не найдено'}
                    onClick={() => onSelect(item.key)}
                  >
                    <span className="n">{item.n}</span>
                    <span>
                      <span className="lab">
                        {item.kind === 'mark'
                          ? item.key.startsWith('seal')
                            ? 'Графическая отметка · печать'
                            : 'Графическая отметка · подпись'
                          : item.label}
                      </span>
                      <br />
                      <span className={item.found ? 'val' : 'val empty'}>
                        {item.found ? displayValue(item.value ?? '', expert) : item.kind === 'mark' ? 'Не обнаружена' : 'не найдено'}
                      </span>
                    </span>
                    <span className={item.found ? 'tag tag--ok' : 'tag tag--miss'}>{item.found ? 'есть' : 'нет'}</span>
                    {expert ? (
                      <span className="tech" style={{ display: 'block' }}>
                        {item.key}
                        {item.kind === 'field'
                          ? ` · уверенность ${Math.round(item.confidence * 100)}%`
                          : ` · метод ${item.method || 'n/a'} · ${Math.round(item.confidence * 100)}%`}
                        {item.box ? ` · [${item.box.x}, ${item.box.y}, ${item.box.width}, ${item.box.height}] px` : ''}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
            {isMark ? (
              <p className="hint">
                Проверяется графический элемент, а не надпись «Подпись» или «М.П.» <RuleChip rule="2.3" />
              </p>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
