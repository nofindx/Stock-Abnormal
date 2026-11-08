// Exercise real page lifecycles with a Shanghai clock and a deterministic timer
// queue. No onShow is sent when advancing a continuously visible page.
const assert = require('assert')
const api = require('../utils/api')
const { businessDateKey, businessBoundaryKey } = require('../utils/business-boundary')
const NativeDate = Date
const nativeSetTimeout = setTimeout
const nativeClearTimeout = clearTimeout
let clock, timerId = 0, definition
const timers = new Map()
const app = { globalData: {} }
global.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])) }
  static now() { return clock }
}
global.setTimeout = (fn, delay) => {
  const id = ++timerId
  timers.set(id, { fn, due: clock + delay })
  return id
}
global.clearTimeout = id => timers.delete(id)
global.Page = value => { definition = value }
global.getApp = () => app
global.wx = { getStorageSync: () => [], setStorageSync() {}, showToast() {}, stopPullDownRefresh() {} }
const tick = () => new Promise(resolve => setImmediate(resolve))
const at = value => { clock = NativeDate.parse(value) }
const event = scope => ({ currentTarget: { dataset: { scope } } })
function page(file) {
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}
async function advance(value) {
  at(value)
  // A sleeping device may deliver an expired timer late. The real clock is
  // consulted on firing, and no intermediate phases are replayed.
  const due = [...timers].filter(([, timer]) => timer.due <= clock)
  for (const [id, timer] of due) {
    if (!timers.has(id)) continue
    timers.delete(id)
    timer.fn()
  }
  await tick()
}
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function run() {
  let reads = [], quotes = 0, weekend = false
  api.getPredictions = async scope => {
    reads.push(scope)
    const before = clock < NativeDate.parse('2026-11-06T09:00:00+08:00')
    return { items: before ? [{ ts_code: 'old' }] : [], nextDayAvailable: before || weekend, realtimeRefreshAllowed: false }
  }
  api.refreshPredictions = async () => { quotes++; return { items: [] } }
  at('2026-11-06T08:59:59+08:00')
  const prediction = page('./prediction/index')
  await prediction.onLoad()
  prediction.onShow()
  prediction.switchScope(event('next_day'))
  await tick()
  await advance('2026-11-06T09:00:01+08:00')
  assert.strictEqual(prediction.data.scope, 'today', 'visible next-day page migrates using server availability')
  assert.strictEqual(prediction.data.nextDayAvailable, false)
  assert.deepStrictEqual(reads, ['today', 'next_day', 'next_day', 'today'])
  assert.strictEqual(quotes, 0, 'a boundary must never trigger S2')
  const readCount = reads.length
  prediction.onShow()
  await tick()
  assert.strictEqual(reads.length, readCount, 'timer followed by onShow does not duplicate S1')
  await advance('2026-11-06T09:15:01+08:00')
  assert.strictEqual(reads.length, readCount + 1, 'dataset opening is a separate S1 boundary')
  await advance('2026-11-06T15:00:01+08:00')
  assert.strictEqual(reads.length, readCount + 2, 'close is an S1 boundary')
  prediction.onHide()
  assert.strictEqual(timers.size, 0, 'hidden prediction page cancels its boundary timer')
  await advance('2026-11-07T10:00:00+08:00')
  assert.strictEqual(reads.length, readCount + 2)
  weekend = true
  prediction.onShow()
  await tick()
  prediction.switchScope(event('next_day'))
  await tick()
  await advance('2026-11-07T15:00:01+08:00')
  assert.strictEqual(prediction.data.scope, 'next_day', 'weekend availability is decided by backend, not local time')
  assert.strictEqual(quotes, 0)
  prediction.onUnload()

  // A slow refresh from before 09:00 cannot restore stale cards afterwards.
  at('2026-11-09T08:59:59+08:00')
  api.getPredictions = async () => ({ items: [{ ts_code: 'current' }], nextDayAvailable: true })
  const oldRefresh = deferred()
  api.refreshPredictions = () => { quotes++; return oldRefresh.promise }
  const race = page('./prediction/index')
  await race.onLoad()
  race.onShow()
  const refresh = race.refresh()
  await advance('2026-11-09T09:00:01+08:00')
  oldRefresh.resolve({ items: [{ ts_code: 'stale' }] })
  await refresh
  assert.strictEqual(race.data.items[0].ts_code, 'current')
  assert.strictEqual(race.data.refreshing, false)
  race.onUnload()

  // First-open S1 finishing after a boundary must not launch its old S2.
  at('2026-11-09T14:59:59+08:00')
  const oldRead = deferred()
  let calls = 0
  api.getPredictions = () => ++calls === 1 ? oldRead.promise : Promise.resolve({ items: [] })
  const opening = page('./prediction/index')
  const initial = opening.onLoad()
  opening.onShow()
  const quotesBefore = quotes
  await advance('2026-11-09T15:00:01+08:00')
  oldRead.resolve({ items: [{ ts_code: 'stale' }], realtimeRefreshAllowed: true })
  await initial
  assert.strictEqual(quotes, quotesBefore)
  assert.deepStrictEqual(opening.data.items, [])
  opening.onUnload()

  // Failure at a boundary clears expired cards and does not spin on onShow.
  at('2026-11-09T14:59:59+08:00')
  let failingReads = 0
  api.getPredictions = async () => {
    if (++failingReads > 1) throw new Error('offline')
    return { items: [{ ts_code: 'expired' }] }
  }
  const failing = page('./prediction/index')
  await failing.onLoad()
  failing.onShow()
  await advance('2026-11-09T15:00:01+08:00')
  assert.deepStrictEqual(failing.data.items, [])
  assert.ok(failing.data.error)
  assert.strictEqual(failing.data.loading, false)
  failing.onShow()
  await tick()
  assert.strictEqual(failingReads, 2, 'a failed boundary read cannot create an onShow retry loop')
  failing.onUnload()

  at('2026-11-09T08:59:59+08:00')
  let stockReads = 0, stockQuotes = 0, delayedStock
  api.getStockDetail = code => {
    stockReads++
    return delayedStock ? delayedStock.promise : Promise.resolve({ ts_code: code, serial: stockReads, realtimeRefreshAllowed: true })
  }
  api.refreshStockDetail = async code => { stockQuotes++; return { ts_code: code } }
  const stock = page('./stock/index')
  stock.renderStockDetail = function(detail, monitor, selected, requestId) {
    if (requestId === this.detailRequestId) this.setData({ detail, detailLoading: false })
  }
  stock.onLoad({})
  await stock.chooseStock({ ts_code: '301313.SZ', name: '凡拓数创' })
  stock.onShow()
  assert.deepStrictEqual([stockReads, stockQuotes], [1, 1])
  await advance('2026-11-09T09:00:01+08:00')
  assert.deepStrictEqual([stockReads, stockQuotes], [2, 1], 'visible stock page reloads S1 only')
  stock.onHide()
  at('2026-11-10T10:00:00+08:00')
  app.globalData.pendingStock = { ts_code: '301313.SZ', name: '凡拓数创' }
  stock.onShow()
  await tick()
  assert.deepStrictEqual([stockReads, stockQuotes], [3, 1], 'same-stock navigation cannot bypass cross-day invalidation')
  assert.strictEqual(stock.data.visibleDays, 2)
  stock.onShow()
  await tick()
  assert.strictEqual(stockReads, 3)
  delayedStock = deferred()
  await advance('2026-11-10T15:00:01+08:00')
  assert.strictEqual(stock.data.detail, null, 'old detail and simulator must be invalidated immediately')
  assert.strictEqual(stock.data.simulationReady, false)
  stock.onUnload()
  delayedStock.resolve({ ts_code: 'stale' })
  await tick()
  assert.strictEqual(stock.data.detail, null, 'unloaded page rejects a late S1 result')

  at('2026-11-06T23:59:59+08:00')
  let monitorReads = 0
  api.getMonitor = async () => ({ items: ++monitorReads === 1 ? [{ name: 'expired', days: 0 }] : [] })
  const monitor = page('./monitor/index')
  monitor.onLoad()
  monitor.onShow()
  await tick()
  assert.strictEqual(monitor.data.items.length, 1)
  await advance('2026-11-07T00:00:01+08:00')
  assert.strictEqual(monitorReads, 2)
  assert.deepStrictEqual(monitor.data.items, [], 'visible monitor page removes expired cards at midnight')
  monitor.onHide()
  await advance('2026-11-08T10:00:00+08:00')
  assert.strictEqual(monitorReads, 2)
  monitor.onShow()
  await tick()
  assert.strictEqual(monitorReads, 3, 'background resume catches missed date boundary')
  monitor.onUnload()
  assert.strictEqual(timers.size, 0, 'all page timers are released')

  assert.strictEqual(businessDateKey(new NativeDate('2026-11-06T16:00:00Z')), '20261107')
  assert.strictEqual(businessBoundaryKey(new NativeDate('2026-11-06T01:15:00Z')), '20261106:session')
  console.log('business boundary tests passed: visible edges, resume, same-stock, availability, races, timer cleanup, Shanghai timezone')
}
run().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => {
  global.Date = NativeDate
  global.setTimeout = nativeSetTimeout
  global.clearTimeout = nativeClearTimeout
})
