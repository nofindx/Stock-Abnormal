// Audit probe: exit 1 means the observed page behavior misses the stated policy.
// Uses mocked API data and the real local page definitions; no cloud requests.
const path = require('path')
const root = path.resolve(__dirname, '../../..')
const api = require(path.join(root, 'miniprogram/utils/api'))
let definition
let state = {}
global.Page = value => { definition = value }
global.getApp = () => ({ globalData: state })
global.wx = { getStorageSync: () => [], setStorageSync() {}, showToast() {}, stopPullDownRefresh() {} }
const NativeDate = Date
let clock = NativeDate.parse('2026-11-06T08:59:59+08:00')
global.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])) }
  static now() { return clock }
}
function page(file) {
  require(path.join(root, file))
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
async function run() {
  const scopes = []
  api.getPredictions = async scope => {
    scopes.push(scope)
    return { items: [], realtimeRefreshAllowed: false,
      nextDayAvailable: clock < NativeDate.parse('2026-11-06T09:00:00+08:00') }
  }
  const prediction = page('miniprogram/pages/prediction/index')
  await prediction.onLoad()
  prediction.switchScope({ currentTarget: { dataset: { scope: 'next_day' } } })
  await tick()
  clock = NativeDate.parse('2026-11-06T09:00:01+08:00')
  prediction.onShow()
  await tick()
  const nextDay = {
    id: 'L03-NEXT-TAB-CLOSED-AT-0900',
    status: prediction.data.scope === 'today' ? 'pass' : 'fail',
    expected: { scope: 'today', nextDayAvailable: false },
    actual: { scope: prediction.data.scope, nextDayAvailable: prediction.data.nextDayAvailable,
      lastRequestedScope: scopes[scopes.length - 1] }
  }
  const stock = page('miniprogram/pages/stock/index')
  stock.data.selected = { ts_code: '301313.SZ' }
  stock.data.detail = { ts_code: '301313.SZ' }
  stock._stockBusinessDate = '20261106'
  clock = NativeDate.parse('2026-11-07T10:00:00+08:00')
  state.pendingStock = { ts_code: '301313.SZ' }
  let resets = 0
  let reads = 0
  stock.resetSimulationInputs = () => { resets++ }
  stock.reloadStockS1ForBoundary = () => { reads++ }
  stock.onShow()
  const sameStock = {
    id: 'L02-SAME-STOCK-AFTER-DATE-CHANGE',
    status: reads === 1 && stock._stockBusinessDate === '20261107' ? 'pass' : 'fail',
    expected: { s1Reads: 1, businessDate: '20261107' },
    actual: { simulationResets: resets, s1Reads: reads, businessDate: stock._stockBusinessDate }
  }
  const checks = [sameStock, nextDay]
  console.log(JSON.stringify({ auditDate: '20261108', checks }, null, 2))
  process.exitCode = checks.some(check => check.status === 'fail') ? 1 : 0
}
run().catch(error => { console.error(error.message); process.exitCode = 2 })
  .finally(() => { global.Date = NativeDate })
