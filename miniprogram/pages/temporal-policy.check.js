// T8 product-policy probe. Exit 1 is an observed release gap, not a passing
// regression assertion for the old behavior. Uses actual page lifecycle code.
const api = require('../utils/api')
const fs = require('fs')
const predictionTemplate = fs.readFileSync(require('path').join(__dirname, 'prediction/index.wxml'), 'utf8')
let definition
global.Page = value => { definition = value }
global.getApp = () => ({ globalData: {} })
global.wx = { getStorageSync: () => [], setStorageSync() {}, stopPullDownRefresh() {}, showToast() {} }
const NativeDate = Date
let clock = NativeDate.parse('2026-11-06T08:59:59+08:00')
global.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])) }
  static now() { return clock }
}
function page(path) {
  require(path)
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

async function run() {
  let reads = 0, quotes = 0
  const cutoff = NativeDate.parse('2026-11-06T09:00:00+08:00')
  api.getPredictions = async () => {
    reads++
    return { items: clock < cutoff ? [{ ts_code: '002724.SZ', ruleKey: 'severe_10d', triggered: true }] : [],
      realtimeRefreshAllowed: false, nextDayAvailable: clock < cutoff }
  }
  api.refreshPredictions = async () => { quotes++; return { items: [] } }
  const prediction = page('./prediction/index')
  await prediction.onLoad()
  clock = cutoff
  await prediction.onShow()
  await tick()
  await tick()
  const predictionResult = { id: 'T8-OPEN-PAGE-0900',
    status: (prediction.data.items.length === 0 || prediction.data.dataUnavailable) && quotes === 0 ? 'pass' : 'fail',
    expected: 'invalidate old current-day conclusions at business boundary; do not repeat automatic S2',
    actual: { s1Reads: reads, s2Requests: quotes, oldTriggeredCards: prediction.data.items.length } }

  clock = NativeDate.parse('2026-11-06T23:59:59+08:00')
  let monitorReads = 0
  api.getMonitor = async () => {
    monitorReads++
    return { items: clock < NativeDate.parse('2026-11-07T00:00:00+08:00')
      ? [{ symbol: '301313', name: '凡拓数创', riskTone: 'severe-10d', days: 0 }] : [], refreshing: false }
  }
  const monitor = page('./monitor/index')
  monitor.onLoad()
  await tick()
  clock = NativeDate.parse('2026-11-07T00:00:00+08:00')
  await monitor.onShow()
  await tick()
  const monitorResult = { id: 'T8-OPEN-MONITOR-MIDNIGHT',
    status: monitor.data.items.length === 0 || monitor.data.error ? 'pass' : 'fail',
    expected: 'remove or explicitly invalidate expired facts after business date changes',
    actual: { reads: monitorReads, expiredCardsRetained: monitor.data.items.length } }
  const { refreshFailureMessage } = require('./prediction/index')
  const failureMessage = refreshFailureMessage({ refresh_state: 'failed', result_state: 'stale' })
  const failureResult = { id: 'T3-FAILED-STALE-PAGE',
    status: failureMessage && predictionTemplate.includes('{{error}}') ? 'pass' : 'fail',
    expected: 'page visibly distinguishes failed/stale S2 from a successful refresh',
    actual: { message: failureMessage, errorBoundToVisibleTemplate: predictionTemplate.includes('{{error}}') } }
  const checks = [predictionResult, monitorResult, failureResult]
  console.log(JSON.stringify({ checks }, null, 2))
  process.exitCode = checks.some(x => x.status === 'fail') ? 1 : 0
}
run().catch(error => { console.error(error.message); process.exitCode = 2 }).finally(() => { global.Date = NativeDate })
