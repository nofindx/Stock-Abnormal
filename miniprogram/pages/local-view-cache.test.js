const assert = require('assert')
const api = require('../utils/api')
const pdf = require('../utils/open-pdf')
const { client } = require('../utils/ads-config')
const NativeDate = Date
let clock = NativeDate.parse('2026-11-12T16:30:00+08:00'), definition
global.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])) }
  static now() { return clock }
}
global.Page = value => { definition = value }
global.getApp = () => ({ globalData: {} })
global.wx = { getStorageSync: () => [], setStorageSync() {}, showToast() {}, stopPullDownRefresh() {} }
function page(file) {
  require(file)
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}
const detail = (code, price = '10') => ({ ts_code: code, name: '测试', symbol: code.split('.')[0],
  tradeDate: '20261112', currentPrice: price, change: '+1.00%', realtimeRefreshAllowed: true,
  futureTradeDates: ['20261113', '20261116'], simulationApplicability: { applicable: true },
  simulationBase: { stock: [1, 2], index: [0, 0], dates: ['20261111', '20261112'] },
  stockState: { marketDataState: 'complete' }, dataQuality: { isComplete: true, ruleFacts: { status: 'complete' } },
  warnings: [], monitoringFacts: [], read_token: { value: 'saved-' + code },
  nextDayForecast: { available: true, baseTradeDate: '20261112', targetTradeDate: '20261113', rules: [] } })
async function run() {
  client.policy = async () => ({ mode: 'free', ready: true })
  let reads = 0, quotes = 0
  api.getStockDetail = async code => { reads++; return detail(code) }
  api.refreshStockDetail = async code => { quotes++; return detail(code, String(10 + quotes)) }
  const stock = page('./stock/index'); stock.onLoad({})
  const a = { ts_code: '600825.SH', name: '新华传媒' }, b = { ts_code: '002724.SZ', name: '海洋王' }
  await stock.chooseStock(a); await stock.chooseStock(b)
  assert.deepStrictEqual([reads, quotes], [2, 2])
  await stock.chooseStock(a)
  assert.deepStrictEqual([reads, quotes], [2, 2], 'returning to a stock does not repeat S1/S2')
  assert.strictEqual(stock.data.detail.currentPrice, '11')
  await stock.onPullDownRefresh()
  await stock.chooseStock(b); await stock.chooseStock(a)
  assert.deepStrictEqual([reads, quotes], [2, 3])
  assert.strictEqual(stock.data.detail.currentPrice, '13', 'latest manual quote replaces the saved view')
  assert.strictEqual(stock.data.visibleDays, 2, 'reentering a stock still resets the simulation session')
  stock.saveStockView({ ...detail(a.ts_code), dataQuality: { isComplete: false } })
  assert.strictEqual(stock._stockViews.has(a.ts_code), false, 'unavailable data cannot resurrect old available cache')
  for (let i = 0; i < 12; i++) stock.saveStockView(detail(String(600000 + i) + '.SH'))
  assert.strictEqual(stock._stockViews.size, 8, 'bounded cache cannot grow with unlimited stock searches')
  clock = NativeDate.parse('2026-11-13T00:00:01+08:00')
  await stock.chooseStock(b)
  assert.deepStrictEqual([reads, quotes], [3, 4], 'next day must reacquire stock data and quote')
  stock.onUnload(); assert.strictEqual(stock._stockViews.size, 0)

  let monitorReads = 0, opened = [], fail = false
  api.getMonitor = async options => {
    assert.strictEqual(options.type, 'all'); monitorReads++
    if (fail) throw Error('offline')
    return { updatedAt: 'today', items: [
      { monitorKey: 'one', sourceId: 'notice1', ts_code: a.ts_code, symbol: '600825', name: '新华传媒',
        riskTone: 'ordinary', days: monitorReads === 1 ? 7 : 1, pdfAvailable: true, sourceUrl: 'https://example.test/pdf-' + monitorReads },
      { monitorKey: 'two', ts_code: b.ts_code, symbol: '002724', name: '海洋王', riskTone: 'severe-10d', days: 2 }
    ] }
  }
  pdf.openPdf = async url => { opened.push(url) }
  const monitor = page('./monitor/index'); await monitor.onLoad()
  const select = type => monitor.selectType({ currentTarget: { dataset: { type } } })
  select('risk'); assert.strictEqual(monitor.data.items.length, 1)
  select('all'); select('severe'); assert.strictEqual(monitor.data.items[0].symbol, '002724')
  assert.strictEqual(monitorReads, 1, 'monitor filters share a single full list')
  select('risk')
  await monitor.openSource({ currentTarget: { dataset: { url: 'https://example.test/pdf-1' } } })
  assert.deepStrictEqual(opened, ['https://example.test/pdf-1'])
  assert.strictEqual(monitorReads, 1, 'fresh PDF URL needs no extra list request')
  clock += 241000
  await monitor.openSource({ currentTarget: { dataset: { url: 'https://example.test/pdf-1' } } })
  assert.strictEqual(monitorReads, 2, 'expired PDF URL is renewed on demand')
  assert.strictEqual(opened[1], 'https://example.test/pdf-2')
  select('all'); select('risk')
  assert.strictEqual(monitor.data.items[0].days, '01', 'all filters reflect the same updated result')
  clock += 241000; fail = true
  await monitor.openSource({ currentTarget: { dataset: { url: 'https://example.test/pdf-2' } } })
  assert.strictEqual(opened.length, 2, 'failed URL renewal must not download an expired address')
  fail = false; clock = NativeDate.parse('2026-11-14T00:00:01+08:00')
  await monitor.selectType({ currentTarget: { dataset: { type: 'all' } } })
  assert.strictEqual(monitorReads, 4, 'next-day filter action reloads even if onShow/timer was delayed')
  monitor.onUnload()
  console.log('local page cache tests passed: stock reuse/LRU/manual/date/quality; monitor local filters/unified countdown/PDF renewal/date')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
