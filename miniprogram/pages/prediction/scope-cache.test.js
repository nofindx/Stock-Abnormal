const assert = require('assert')
const api = require('../../utils/api')
const { client } = require('../../utils/ads-config')
const NativeDate = Date
let clock = NativeDate.parse('2026-11-12T16:30:00+08:00'), definition
global.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])) }
  static now() { return clock }
}
global.Page = value => { definition = value }
global.getApp = () => ({ globalData: {} })
global.wx = { showToast() {}, stopPullDownRefresh() {} }
function page() {
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}
const event = scope => ({ currentTarget: { dataset: { scope } } })
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { resolve, promise } }
async function run() {
  client.policy = async () => ({ mode: 'free', ready: true })
  let reads = [], refreshes = [], pending = null, refreshFailure = false, unavailable = false
  const result = (scope, suffix = '') => ({ items: [{ ts_code: scope + suffix, ruleKey: 'severe_10d' }],
    targetTradeDate: scope === 'today' ? '20261112' : '20261113', nextDayAvailable: true,
    quoteUpdatedAt: '2026-11-12T16:00:00', read_token: { value: 'token-' + scope }, dataUnavailable: unavailable })
  api.getPredictions = async scope => { reads.push(scope); return result(scope) }
  api.refreshPredictions = async (scope, saved) => {
    refreshes.push({ scope, token: saved.read_token.value })
    if (refreshFailure) throw Error('offline')
    return pending ? pending.promise : result(scope, '-fresh')
  }
  require('./index')
  const p = page(); await p.onLoad()
  await p.switchScope(event('next_day'))
  const before = reads.length
  await p.switchScope(event('today'))
  assert.strictEqual(p.data.items[0].ts_code, 'today')
  assert.strictEqual(p.data.loading, false)
  await p.switchScope(event('next_day'))
  assert.strictEqual(p.data.items[0].ts_code, 'next_day')
  assert.strictEqual(reads.length, before, 'returning to loaded tabs performs zero business GETs')
  assert.strictEqual(refreshes.length, 0, 'tab switching performs zero S2s')
  await p.refresh()
  assert.strictEqual(refreshes[0].token, 'token-next_day', 'manual refresh uses that tab authorization')
  await p.switchScope(event('today')); await p.switchScope(event('next_day'))
  assert.strictEqual(p.data.items[0].ts_code, 'next_day-fresh', 'restore latest manual result, not S1')
  refreshFailure = true; await p.refresh(); refreshFailure = false
  await p.switchScope(event('today')); await p.switchScope(event('next_day'))
  assert.match(p.data.error, /刷新失败/, 'cached failure warning cannot become a false fresh result')

  // An old pending S2 must not replace a cached scope restored after two switches.
  pending = deferred(); const late = p.refresh()
  await p.switchScope(event('today')); await p.switchScope(event('next_day'))
  pending.resolve(result('next_day', '-obsolete')); await late; pending = null
  assert.strictEqual(p.data.items[0].ts_code, 'next_day-fresh')

  // A hidden page or delayed timer must still invalidate at the next tap.
  clock = NativeDate.parse('2026-11-13T00:00:01+08:00')
  await p.switchScope(event('today'))
  assert(reads.length > before, 'a new business boundary requires a new S1')
  assert.strictEqual(p.cachedScope('next_day') !== null && p.cachedScope('next_day').boundary.startsWith('20261112'), false)
  p.onUnload(); assert.deepStrictEqual(p._scopeViews, {})

  // Unavailable results are not accepted as reusable successful views.
  unavailable = true; const q = page(); await q.onLoad()
  assert.strictEqual(Boolean(q.cachedScope('today')), false)
  q.onUnload()
  console.log('prediction scope cache tests passed: zero-read revisits, per-tab tokens, latest S2, failure warning, late response, boundary, unavailable, unload')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
