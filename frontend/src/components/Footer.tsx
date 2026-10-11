import { useGuide } from '../lib/session'

/** Подвал на всех экранах: команда, год, репозиторий и версия — всё тянем из конфига. */
export default function Footer() {
  const { config } = useGuide()
  const team = config?.team_name || 'Название команды'
  const year = config?.year ?? new Date().getFullYear()
  return (
    <footer className="site-foot">
      <div className="site-foot__in">
        <span>
          © Команда «{team}», {year}
        </span>
        {config?.repo_url ? (
          <a href={config.repo_url} target="_blank" rel="noreferrer">
            Репозиторий
          </a>
        ) : null}
        {config?.version ? <span className="mono">v{config.version}</span> : null}
      </div>
    </footer>
  )
}
