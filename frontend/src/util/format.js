// Shared formatting. Timestamps from the API are ISO 8601 UTC strings.

export function shortGuid (guid) {
  if (!guid) return 'Not near a base'
  return guid.slice(0, 8)
}

export function relativeTime (iso) {
  if (!iso) return 'never'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return iso
  const seconds = Math.round((Date.now() - then.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function formatWindow (iso) {
  if (!iso) return 'not scheduled'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return iso
  const today = new Date()
  const sameDay = when.toDateString() === today.toDateString()
  const time = when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return `today ${time}`
  const tomorrow = new Date(today)
  tomorrow.setDate(today.getDate() + 1)
  if (when.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`
  return when.toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function coordinates (chest) {
  if (chest.x === null || chest.x === undefined) return '—'
  // Palworld's world units are large; whole numbers are enough to navigate by.
  return `${Math.round(chest.x)}, ${Math.round(chest.y)}`
}

export const EDIT_STATUS_COLORS = {
  queued: 'warning',
  applying: 'info',
  applied: 'positive',
  failed: 'negative',
  cancelled: 'grey-7',
}
