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
  // S1 carries reset history and a Friday quote through the weekend. Rendering
  // must select facts/count down against today's Shanghai date, not quote date.
  const expired = { symbol: '600825', riskTone: 'severe-10d', monitorStartDate: '20261012', monitorEndDate: '20261023' }
  const ordinary = { symbol: '600825', riskTone: 'ordinary', monitorStartDate: '20261113', monitorEndDate: '20261126' }
  clock = NativeDate.parse('2026-11-15T12:00:00+08:00')
  stock.renderStockDetail({ ...detail(a.ts_code), tradeDate: '20261113', monitoring: expired,
    monitoringFacts: [expired, ordinary] }, { items: [] }, a, stock.detailRequestId)
  assert.strictEqual(stock.data.detail.monitoring.riskTone, 'ordinary')
  assert.strictEqual(stock.data.detail.monitoringSummary.dateText, '20261113-20261126')
  assert.strictEqual(stock.data.detail.monitoringSummary.remainingText, '11')
  clock = NativeDate.parse('2026-11-27T00:00:01+08:00')
  stock.renderStockDetail({ ...detail(a.ts_code), tradeDate: '20261126', monitoring: ordinary,
    monitoringFacts: [expired, ordinary], monitoringStatus: 'active' }, { items: [] }, a, stock.detailRequestId)
  assert.strictEqual(stock.data.detail.monitoring, null, 'expired primary fact must also be removed')
  assert.strictEqual(stock.data.detail.monitoringStatus, 'none')
  assert.strictEqual(stock.data.detail.monitoringSummary, null)
  clock = NativeDate.parse('2026-11-15T12:00:00+08:00')
  const records = ['delisting-warning', 'severe-30d', 'severe-10d', 'ordinary'].map((riskTone, i) => ({
    sourceId: 'fact' + i, riskTone, active: true, monitorStartDate: '20261113', monitorEndDate: '20261126'
  }))
  records.push({ sourceId: 'pending', riskTone: 'severe-10d', active: false, confirmedPendingStart: true,
    monitorStartDate: '20261116', monitorEndDate: '20261127' })
  records[2].recognitionBasis = [{ text: '公告认定：连续9个交易日内出现4次同向异常波动' }]
  const { stockSaveSignature } = require('../utils/stock-save')
  for (const [tradingState, label] of [['normal', null], ['suspended', '停牌'], ['terminated', '终止上市'], ['unknown', '交易状态未知']]) {
    const state = { ...detail(a.ts_code), monitoring: records[0], monitoringRecords: records,
      // The raw historical list includes duplicates. It must not be merged
      // again into the backend's already selected and deduplicated list.
      monitoringFacts: [records[0], records[0]], stockState: { marketDataState: 'complete',
        tradingState, tradingStateReason: '状态证据' } }
    stock.renderStockDetail(state, { items: [] }, a, stock.detailRequestId)
    assert.strictEqual(stock.data.detail.monitoringSummaries.length, 5)
    assert.strictEqual(stock.data.detail.monitoring.riskTone, 'delisting-warning')
    assert.strictEqual(stock.data.detail.tradingBanner && stock.data.detail.tradingBanner.label, label)
    assert.strictEqual(stock.data.detail.monitoringSummaries[4].badgeText, '将进入重点监控')
    assert.strictEqual(stock.data.detail.monitoringSummaries[0].remainingText, '11')
    assert.strictEqual(stock.data.detail.monitoringSummaries[2].ruleText, records[2].recognitionBasis[0].text)
  }
  const signature = stockSaveSignature(stock.data)
  const changed = JSON.parse(JSON.stringify(stock.data))
  changed.detail.monitoringSummaries[3].ruleText = '另一条普通风险提示'
  assert.notStrictEqual(stockSaveSignature(changed), signature, 'saving must track changes to every stacked fact')
  changed.detail = { ...stock.data.detail, tradingBanner: null }
  assert.notStrictEqual(stockSaveSignature(changed), signature, 'saving must track trading state changes')
  const markup = require('fs').readFileSync(require('path').join(__dirname, 'stock/index.wxml'), 'utf8')
  assert(markup.includes('wx:for="{{detail.monitoringSummaries}}"'))
  assert(markup.includes('data-export-text="{{monitorEntry.ruleText}}"'))
  assert(markup.includes('data-export-text="{{detail.tradingBanner.label}}"'))
  const stockBaseline = { ...detail(a.ts_code), calculationBasisId: 'same-day-before-reset' }
  stock.renderStockDetail(stockBaseline, { items: [] }, a, stock.detailRequestId)
  stock.data.inputRows[0].stock = '5'
  stock.renderStockDetail(stockBaseline, { items: [] }, a, stock.detailRequestId)
  assert.strictEqual(stock.data.inputRows[0].stock, '5', 'unchanged S1 baseline preserves simulation input')
  stock.resetSimulationInputs()
  stock.editRow({ currentTarget: { dataset: { row: 0, field: 'stock' } }, detail: { value: '6' } })
  await stock.expandSimulation()
  assert.strictEqual(stock.data.visibleDays, 10)
  stock.renderStockDetail(stockBaseline, { items: [] }, a, stock.detailRequestId)
  assert.strictEqual(stock.data.inputRows[0].stock, '6', 'same-stock reentry and subsequent unchanged S1 retain newly entered input')
  assert.strictEqual(stock.data.visibleDays, 10, 'same-stock reentry must use the complete baseline identity when resetting the session')
  stock.renderStockDetail({ ...stockBaseline, calculationBasisId: 'same-day-after-reset' }, { items: [] }, a, stock.detailRequestId)
  assert.strictEqual(stock.data.inputRows[0].stock, '', 'same-day reset requires a new default simulation baseline')
  assert.strictEqual(stock.data.visibleDays, 2)
  clock = NativeDate.parse('2026-11-13T00:00:01+08:00')
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
