const assert = require('assert')
const api = require('../utils/api')
let pageDefinition
global.Page = definition => { pageDefinition = definition }
const app = { globalData: {} }
global.getApp = () => app
global.wx = { getStorageSync: () => [], setStorageSync() {}, stopPullDownRefresh() {}, showToast() {} }

function page(path) {
  delete require.cache[require.resolve(path)]
  require(path)
  return { ...pageDefinition, data: JSON.parse(JSON.stringify(pageDefinition.data)),
    setData(values, done) { Object.assign(this.data, values); if (done) done() } }
}

async function run() {
  let reads = 0, quotes = 0
  api.getPredictions = async () => { reads++; return { items: [], realtimeRefreshAllowed: true } }
  api.refreshPredictions = async () => { quotes++; return { items: [] } }
  const prediction = page('./prediction/index')
  const firstOpen = prediction.onLoad()
  prediction.onShow()
  prediction.onShow()
  await firstOpen
  assert.deepStrictEqual([reads, quotes], [1, 1], 'first open does S1 followed by one S2')
  prediction.onShow()
  await Promise.resolve()
  assert.deepStrictEqual([reads, quotes], [1, 1], 'returning to the tab must retain the current view')
  await prediction.onPullDownRefresh()
  assert.deepStrictEqual([reads, quotes], [1, 2], 'manual refresh remains enabled')
  let finishPrediction
  api.refreshPredictions = (_scope, _previous, options) => {
    options.onS1({ items: [{ ts_code: 'NEW', predictionKey: 'new-key', name: '新名单' }], monitoringFacts: [], realtimeRefreshAllowed: true })
    return new Promise(resolve => { finishPrediction = resolve })
  }
  const progressingPrediction = page('./prediction/index')
  const updatingPrediction = progressingPrediction.onPullDownRefresh()
  assert.strictEqual(progressingPrediction.data.items[0].ts_code, 'NEW', 'new S1 list displays while quotes are pending')
  assert.strictEqual(progressingPrediction.data.refreshing, true, 'showing S1 must not end the S2 loading indicator')
  finishPrediction({ items: [{ ts_code: 'NEW', predictionKey: 'new-key', name: '新名单', change: '+1.00%' }] })
  await updatingPrediction
  assert.strictEqual(progressingPrediction.data.refreshing, false)
  api.refreshPredictions = async () => { quotes++; return { items: [] } }
  prediction.switchScope({ currentTarget: { dataset: { scope: 'next_day' } } })
  await Promise.resolve()
  prediction.switchScope({ currentTarget: { dataset: { scope: 'today' } } })
  await Promise.resolve()
  assert.strictEqual(quotes, 2, 'scope switching must not request realtime quotes')

  api.getPredictions = async () => { throw new Error('offline') }
  const failedOpen = page('./prediction/index')
  await failedOpen.onLoad()
  failedOpen.onShow()
  assert.strictEqual(quotes, 2, 'failed initialization must not create an onShow retry loop')

  let stockReads = 0, stockQuotes = 0
  const result = code => ({ ts_code: code, realtimeRefreshAllowed: true, monitoringFacts: [] })
  api.getStockDetail = async code => { stockReads++; return result(code) }
  api.refreshStockDetail = async code => { stockQuotes++; return result(code) }
  const stock = page('./stock/index')
  stock.renderStockDetail = function(detail) { this.setData({ detail, detailLoading: false, simulationReady: true }) }
  stock.onLoad({})
  await stock.chooseStock({ ts_code: '002724.SZ', name: '海洋王' })
  assert.deepStrictEqual([stockReads, stockQuotes], [1, 1])
  stock.data.simulationExpanded = true
  stock.data.visibleDays = 10
  stock.data.inputRows = [{ stock: '5', index: '1' }]
  app.globalData.pendingStock = { ts_code: '002724.SZ', name: '海洋王' }
  stock.onShow()
  stock.onShow()
  assert.deepStrictEqual([stockReads, stockQuotes], [1, 1], 'returning from a prediction card must reuse the existing detail without another quote request')
  assert.strictEqual(stock.data.simulationExpanded, false, 'opening a stock card resets the simulation to its two-day initial mode')
  assert.strictEqual(stock.data.visibleDays, 2)
  assert.strictEqual(stock.data.inputRows.length, 2)
  assert.strictEqual(stock.data.inputRows[0].stock, '', 'navigation reset must clear prior user inputs')
  assert.strictEqual(stock.data.simulationReady, true, 'the existing detail baseline is recalculated before the page is shown')
  await stock.onPullDownRefresh()
  assert.deepStrictEqual([stockReads, stockQuotes], [1, 2])
  let finishStock
  api.refreshStockDetail = (code, _previous, options) => {
    options.onS1({ ...result(code), currentPrice: '12.34', monitoringFacts: [{ sourceId: 'new-fact' }] })
    return new Promise(resolve => { finishStock = resolve })
  }
  const progressingStock = page('./stock/index')
  progressingStock.data.selected = { ts_code: '002724.SZ', name: '海洋王' }
  progressingStock.detailRequestId = 1
  progressingStock.renderStockDetail = stock.renderStockDetail
  const updatingStock = progressingStock.onPullDownRefresh()
  assert.strictEqual(progressingStock.data.detail.currentPrice, '12.34', 'new S1 renders while the quote refresh is pending')
  assert.strictEqual(progressingStock.data.detail.monitoringFacts[0].sourceId, 'new-fact')
  const activeStock = progressingStock.data.selected.ts_code
  finishStock({ ...result(activeStock), currentPrice: '12.56' })
  await updatingStock
  assert.strictEqual(progressingStock.data.detail.currentPrice, '12.56')
  api.refreshStockDetail = async code => { stockQuotes++; return result(code) }
  await stock.chooseStock({ ts_code: '603278.SH', name: '大业股份' })
  assert.deepStrictEqual([stockReads, stockQuotes], [2, 3], 'opening a different security initializes its quotes once')
  stock.showQuoteRefreshState({ result_state: 'stale', reasons: ['INDEX_QUOTE_UNAVAILABLE'] }, stock.detailRequestId)
  assert.match(stock.data.detailError, /价格已更新.*计算保留/, 'partial success must distinguish display price from calculation')
  stock.showQuoteRefreshState({ result_state: 'stale', reasons: ['REALTIME_SOURCE_UNAVAILABLE'] }, stock.detailRequestId)
  assert.match(stock.data.detailError, /最新行情刷新失败/, 'failed quotes must not appear as a fresh result')

  let monitorReads = 0
  api.getMonitor = async () => { monitorReads++; return { items: [], refreshing: false } }
  const monitor = page('./monitor/index')
  monitor.onLoad()
  await monitor.loadPromise
  monitor.onShow()
  monitor.onShow()
  assert.strictEqual(monitorReads, 1)
  await monitor.onPullDownRefresh()
  assert.strictEqual(monitorReads, 2)
  console.log('page refresh lifecycle tests passed')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
