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
  await stock.chooseStock({ ts_code: '603278.SH', name: '大业股份' })
  assert.deepStrictEqual([stockReads, stockQuotes], [2, 3], 'opening a different security initializes its quotes once')

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
