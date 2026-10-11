import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import type { Badge } from '../lib/badges'
import { BADGE_EVENT } from '../lib/badges'

/** Плашка «Новый значок» над кнопкой помощника; сама пропадает через 3,5 с. */
export default function BadgeToast() {
  const [badge, setBadge] = useState<Badge | null>(null)

  useEffect(() => {
    let timer: number | undefined
    const onBadge = (event: Event) => {
      setBadge((event as CustomEvent<Badge>).detail)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setBadge(null), 3500)
    }
    window.addEventListener(BADGE_EVENT, onBadge)
    return () => {
      window.removeEventListener(BADGE_EVENT, onBadge)
      window.clearTimeout(timer)
    }
  }, [])

  return (
    <div className="badge-toast-wrap" role="status" aria-live="polite">
      <AnimatePresence>
        {badge ? (
          <motion.div
            key={badge.id}
            className="badge-toast"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={{ duration: 0.2 }}
            onClick={() => setBadge(null)}
          >
            <span className="badge-toast__glyph" aria-hidden="true">
              {badge.glyph}
            </span>
            <span>
              <small>Новый значок</small>
              <b>{badge.title}</b>
            </span>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
