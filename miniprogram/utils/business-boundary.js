// Calendar/availability decisions remain on the server. These Shanghai clock
// boundaries only invalidate S1; they never trigger a realtime quote request.
const SHANGHAI_OFFSET = 8 * 60 * 60 * 1000
const DAY = 24 * 60 * 60 * 1000

function businessDateKey(now = new Date()) {
  const local = new Date(now.getTime() + SHANGHAI_OFFSET)
  return `${local.getUTCFullYear()}${String(local.getUTCMonth() + 1).padStart(2, '0')}${String(local.getUTCDate()).padStart(2, '0')}`
}

function businessBoundaryKey(now = new Date()) {
  const local = new Date(now.getTime() + SHANGHAI_OFFSET)
  const minute = local.getUTCHours() * 60 + local.getUTCMinutes()
  const phase = minute < 540 ? 'before-open' : minute < 555 ? 'dataset-open-pending' : minute < 900 ? 'session' : 'after-close'
  return `${businessDateKey(now)}:${phase}`
}

function stopBoundaryWatch(page) {
  if (page.boundaryTimer != null) clearTimeout(page.boundaryTimer)
  page.boundaryTimer = null
}

function startBoundaryWatch(page, check, dailyOnly = false) {
  stopBoundaryWatch(page)
  const schedule = () => {
    const now = Date.now()
    const midnight = Math.floor((now + SHANGHAI_OFFSET) / DAY) * DAY - SHANGHAI_OFFSET
    const edges = dailyOnly ? [DAY] : [9 * 3600000, (9 * 60 + 15) * 60000, 15 * 3600000, DAY]
    const next = edges.map(edge => midnight + edge).find(edge => edge > now)
    page.boundaryTimer = setTimeout(() => {
      page.boundaryTimer = null
      check.call(page)
      schedule()
    }, next - now + 20)
    // Node regression processes should not be kept alive by a page timer.
    if (page.boundaryTimer && page.boundaryTimer.unref) page.boundaryTimer.unref()
  }
  check.call(page)
  schedule()
}

module.exports = { businessDateKey, businessBoundaryKey, startBoundaryWatch, stopBoundaryWatch }
