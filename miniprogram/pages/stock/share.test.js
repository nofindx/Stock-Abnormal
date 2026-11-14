const assert = require('assert')
const fs = require('fs')
const path = require('path')
const share = require('../../utils/stock-share')
const entry = require('../../utils/share-entry')
const api = require('../../utils/api')
const { client } = require('../../utils/ads-config')
let definition
global.Page = value => { definition = value }
const app = { globalData: {} }
global.getApp = () => app
const dates = ['20261116', '20261117', '20261118', '20261119', '20261120', '20261123', '20261124', '20261125', '20261126', '20261127']
function detail(code = '301313.SZ') {
  return { ts_code: code, symbol: code.split('.')[0], name: '凡拓数创', tradeDate: '20261113',
    currentPrice: '32.51', change: '+2.37%', summaryStatus: '暂无异动风险', statusClass: 'safe',
    simulationApplicability: { applicable: true }, futureTradeDates: dates,
    stockState: { marketDataState: 'complete' }, dataQuality: { isComplete: true, ruleFacts: { status: 'complete' } },
    simulationBase: { stock: Array(29).fill(1), index: Array(29).fill(0), dates: [] },
    simulationThresholds: { '10': { up: 100 }, '30': { up: 200 } },
    warnings: [
      { title: '3 日偏离', value: '+3.03%', target: '阈值 +30% / -30%' },
      { title: '10 日同向', up: 2, down: 0, target: '阈值 3次' },
      { title: '10 日偏离', value: '+10.46%', target: '阈值 +100% / -50%' },
      { title: '30 日偏离', value: '+34.78%', target: '阈值 +200% / -70%' }
    ], monitoringFacts: [], nextDayForecast: { available: true, baseTradeDate: '20261113', targetTradeDate: dates[0], rules: [] },
    read_token: { value: 'SENSITIVE_READ_TOKEN' } }
}
const measure = { font: '', measureText(s) { return { width: Array.from(s).length * (Number.parseFloat(this.font.match(/[\d.]+/)[0]) * 0.6) } } }
const drawLog = []
const ctx = { ...measure, fillRect() {}, strokeRect() {}, beginPath() {}, arc() {}, stroke() {},
  fillText(s) { drawLog.push(s) } }
let exportOptions, shareCalls, previews, toasts, menus, exportMode, selectedCanvas
function resetPlatform() {
  shareCalls = []; previews = []; toasts = []; menus = []; exportMode = 'success'
  global.wx = {
    getStorageSync: () => [], setStorageSync() {}, showToast: v => toasts.push(v.title),
    showShareMenu: v => menus.push(v.menus),
    createSelectorQuery: () => ({ in() { return this }, select(selector) { selectedCanvas = selector; return this }, fields() { return this },
      exec(fn) { fn([{ node: canvas }]) } }),
    canvasToTempFilePath(o) { if (selectedCanvas === '#stock-share-cover-canvas') { o.success({ tempFilePath: '/tmp/current-stock-cover.png' }); return }; exportOptions = o; if (exportMode === 'success') o.success({ tempFilePath: '/tmp/result.png' });
      else if (exportMode === 'fail') o.fail({ errMsg: 'export failed' }) },
    showShareImageMenu(o) { shareCalls.push(o); o.success({}) },
    previewImage(o) { previews.push(o); o.success({}) }
  }
}
const canvas = { width: 0, height: 0, getContext: () => ctx }
function page() {
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(v, done) { Object.assign(this.data, v); if (done) done() } }
}
const tick = async () => { for (let i = 0; i < 25; i++) await Promise.resolve() }
async function run() {
  resetPlatform()
  client.policy = async () => ({ mode: 'free', ready: true })
  let reads = 0, quotes = 0
  api.getStockDetail = async code => { reads++; return detail(code) }
  api.refreshStockDetail = async code => { quotes++; return detail(code) }
  require('./index')
  const p = page(); p.onLoad({})
  assert.deepStrictEqual(menus[0], ['shareAppMessage', 'shareTimeline'])
  await p.chooseStock({ ts_code: '301313.SZ', name: '凡拓数创' })
  const original = JSON.stringify(p.data)
  const defaults = p.stockShareSnapshot()
  assert.strictEqual(defaults.rows.length, 2)
  assert.strictEqual(defaults.rows[0].stock, '')
  assert(!JSON.stringify(defaults).includes('SENSITIVE_READ_TOKEN'))
  let plan = share.createSharePlan(defaults, measure)
  assert(plan.commands.some(c => c.value === '—'), 'default simulation input stays visibly unfilled')
  assert(!plan.commands.some(c => c.value === '0%'), 'unfilled inputs must not be rendered as user-entered zeros')
  assert.strictEqual(plan.commands[0].fill, '#FFFFFF')
  assert(plan.commands.some(c => c.value === '模拟计算（假设涨幅）'))
  const captions = plan.commands.filter(c => c.type === 'text').map(c => c.value).join('\n')
  for (const removed of ['分享时结果', '空白输入按', '接收者从', '打开小程序查看']) assert(!captions.includes(removed))
  assert.strictEqual(captions.split('以正式公告为准').length - 1, 1)
  assert.strictEqual(captions.split('行情时间：').length - 1, 1)
  const triggeredPlan = share.createSharePlan({ ...defaults, statusClass: 'monitoring',
    forecast: ['已触发：10 日偏离', '将进入重点监控池，具体以官方公告为准。'] }, measure)
  assert(triggeredPlan.commands.some(c => c.value === '已触发：10 日偏离'))
  assert(triggeredPlan.commands.some(c => c.value === '将进入重点监控池'))
  assert(triggeredPlan.commands.some(c => c.type === 'box' && c.w === 4 && c.fill === '#C83A36'))
  const rawWarnings = p.data.detail.warnings
  rawWarnings.forEach(w => assert(plan.commands.some(c => c.value === w.currentText), 'displayed warning must be preserved'))
  assert.strictEqual(JSON.stringify(p.data), original, 'capture and render do not change page data')
  await p.expandSimulation()
  p.editRow({ currentTarget: { dataset: { row: 0, field: 'stock' } }, detail: { value: '5.38' } })
  p.editRow({ currentTarget: { dataset: { row: 9, field: 'index' } }, detail: { value: '-0.24' } })
  const custom = p.stockShareSnapshot()
  const customSerialized = JSON.stringify(custom)
  assert.strictEqual(custom.rows.length, 2)
  assert.strictEqual(custom.rows[0].stock, '')
  assert.deepStrictEqual(custom.rows, defaults.rows, 'custom ten-day page shares the original two-day empty baseline')
  plan = share.createSharePlan(custom, measure)
  for (const excluded of ['T+10', '5.38%', '-0.24%']) assert(!plan.commands.some(c => c.value === excluded))
  const cover = share.createShareCoverPlan(custom, measure)
  assert.deepStrictEqual([cover.width, cover.height], [750, 600])
  custom.warnings.forEach(w => assert(cover.commands.some(c => c.value === w.value)))
  assert(!cover.commands.some(c => c.type === 'text' && c.value.includes('模拟')))
  p.data.inputRows[0].stock = '99'
  assert.strictEqual(JSON.stringify(custom), customSerialized, 'later edits cannot mutate a captured image')
  const countBefore = [reads, quotes]
  await p.shareStockPage()
  assert.deepStrictEqual([reads, quotes], countBefore, 'sharing never requests S1 or S2')
  assert.strictEqual(p.data.visibleDays, 10)
  assert.strictEqual(p.data.inputRows[0].stock, '99')
  assert.strictEqual(canvas.width, 1, 'release canvas buffer after export')
  assert.strictEqual(shareCalls.length, 1)
  assert.strictEqual(shareCalls[0].needShowEntrance, true)
  assert.strictEqual(p.data.sharingStock, false)
  const route = p.onShareAppMessage()
  const timeline = p.onShareTimeline()
  assert.strictEqual(route.path, '/' + entry.shareEntry(custom.code))
  assert.strictEqual(timeline.query, route.path.split('?')[1])
  assert.strictEqual(route.title, '凡拓数创｜异动计算')
  assert.strictEqual(route.imageUrl, '/tmp/current-stock-cover.png', 'native card uses the current stock status cover')
  const savedCover = p._stockShareCover
  p._stockShareCover = { ...savedCover, code: '600825.SH' }
  assert.strictEqual(p.onShareAppMessage().imageUrl, '/assets/share-stock.png', 'a different stock cover is never reused')
  p._stockShareCover = savedCover
  assert(!route.path.includes('99'))
  for (const forbidden of ['inputRows', 'visibleDays', 'read_token', 'unlock', 'stock=']) assert(!route.path.includes(forbidden))
  assert.strictEqual(entry.parseShareEntry({ from: 'stock_share', ts_code: '../escape' }), null)
  assert.strictEqual(entry.parseShareEntry(null), null)

  exportMode = 'defer'
  const first = p.shareStockPage(); await tick()
  const duplicate = p.shareStockPage(); await duplicate
  assert.strictEqual(p.data.sharingStock, true)
  exportOptions.success({ tempFilePath: '/tmp/duplicate.png' }); await first
  assert.strictEqual(shareCalls.length, 2, 'double tapping produces only one more menu')
  const beforeRace = shareCalls.length
  const raced = p.shareStockPage(); await tick()
  await p.chooseStock({ ts_code: '600825.SH', name: '新华传媒' })
  exportOptions.success({ tempFilePath: '/tmp/old-stock.png' }); await raced
  assert.strictEqual(shareCalls.length, beforeRace, 'late export cannot share the wrong selected stock')
  exportMode = 'fail'; await p.shareStockPage()
  assert.strictEqual(p.data.sharingStock, false)
  assert(toasts.some(t => t.includes('失败')))
  exportMode = 'success'
  wx.showShareImageMenu = o => o.fail({ errMsg: 'showShareImageMenu:fail cancel' })
  const beforeCancel = toasts.length
  await p.shareStockPage()
  assert.strictEqual(toasts.length, beforeCancel, 'cancel does not claim success or show failure')
  assert.strictEqual(previews.length, 0)
  delete wx.showShareImageMenu
  await p.shareStockPage(); assert.strictEqual(previews.length, 1, 'old clients can preview and long-press the full result')
  wx.showShareImageMenu = o => o.fail({ errMsg: 'not supported' })
  await p.shareStockPage(); assert.strictEqual(previews.length, 2, 'unsupported menu falls back to image preview')

  // Warm launch must discard recipient inputs/grants/cache, even for the same stock.
  await p.expandSimulation(); p.data.inputRows[0].stock = '77'
  const beforeWarm = reads
  const previousEpoch = p._simulationEpoch
  app.globalData.pendingStock = entry.parseShareEntry({ from: 'stock_share', ts_code: '600825.SH', visibleDays: '10', stock: '123' })
  p.onShow(); await tick()
  assert.strictEqual(reads, beforeWarm + 1)
  assert.strictEqual(p.data.visibleDays, 2)
  assert.strictEqual(p.data.inputRows[0].stock, '')
  assert(p._simulationEpoch > previousEpoch, 'share entry invalidates the old simulation/reward session')
  p.onUnload()
  const cold = page()
  app.globalData.pendingStock = entry.parseShareEntry({ from: 'stock_share', ts_code: '301313.SZ' })
  const beforeCold = reads
  await cold.onLoad({ from: 'stock_share', ts_code: '301313.SZ', stock: '88', visibleDays: '10' })
  cold.onShow(); await tick()
  assert.strictEqual(reads, beforeCold + 1, 'cold launch consumes app handoff exactly once')
  assert.strictEqual(cold.data.inputRows[0].stock, '')
  assert.strictEqual(cold.data.visibleDays, 2)
  cold.onUnload()
  const invalid = page()
  await invalid.onLoad({ from: 'stock_share', ts_code: '../escape' })
  assert.strictEqual(invalid.data.selected, null, 'invalid share links stay on the safe initial page')
  invalid.onUnload()

  resetPlatform()
  const lifetime = page(); lifetime.onLoad({})
  await lifetime.chooseStock({ ts_code: '301313.SZ', name: '凡拓数创' })
  exportMode = 'defer'
  let late = lifetime.shareStockPage(); await tick()
  lifetime.resetSimulationInputs()
  exportOptions.success({ tempFilePath: '/tmp/old-session.png' }); await late
  assert.strictEqual(shareCalls.length, 0, 'reinitializing the same stock invalidates a late image')
  late = lifetime.shareStockPage(); await tick()
  const NativeDate = Date, nextDay = NativeDate.now() + 86400000
  global.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [nextDay])) }
    static now() { return nextDay }
  }
  try {
    exportOptions.success({ tempFilePath: '/tmp/yesterday.png' }); await late
    assert.strictEqual(shareCalls.length, 0, 'cross-date export cannot open yesterday\'s image')
  } finally { global.Date = NativeDate }
  late = lifetime.shareStockPage(); await tick()
  lifetime.onUnload()
  exportOptions.success({ tempFilePath: '/tmp/closed-page.png' }); await late
  assert.strictEqual(shareCalls.length, 0, 'unloaded pages cannot open a share menu')

  const failed = { ...defaults, simulationReady: false, simulationNotice: '暂不适用', dataNotice: '最新行情刷新失败' }
  const failedPlan = share.createSharePlan(failed, measure)
  assert(failedPlan.commands.some(c => c.value === '暂不适用'))
  assert(failedPlan.commands.some(c => c.value === '最新行情刷新失败'))
  const huge = { ...custom, dataNotice: '数据待核验'.repeat(1000) }
  assert.throws(() => share.createSharePlan(huge, measure), /内容过长/)
  const markup = fs.readFileSync(path.join(__dirname, 'index.wxml'), 'utf8')
  const shareSection = markup.indexOf('class="stock-share-card card"')
  assert(shareSection > markup.indexOf('class="section simulation-section"'))
  assert(shareSection < markup.indexOf('class="legal-footer-links"'))
  assert(markup.includes('分享一下') && markup.includes('分享当前股票状态与异动计算结果'))
  assert(markup.includes('stock-share-card card'))
  assert(markup.includes('class="share-actions"'), 'share buttons use the horizontal action row')
  const stockCss = fs.readFileSync(path.join(__dirname, 'index.wxss'), 'utf8')
  for (const style of ['#78A8F2', '#6597E3', '#D6DFEF', 'height: 76rpx', 'gap: 20rpx']) assert(stockCss.includes(style), `missing share styling: ${style}`)
  assert(stockCss.includes('share-btn--ghost { border: 1rpx solid #D6DFEF; background: #FFFFFF; color: #3A6FD8;'))
  const timelineIcon = fs.readFileSync(path.join(__dirname, '../../assets/weui-share/timeline.svg'), 'utf8')
  assert(timelineIcon.includes('fill="#3A6FD8"'), 'timeline icon matches the button text color')
  for (const channel of ['发给好友', '炫朋友圈']) assert(markup.includes(channel), `missing share channel: ${channel}`)
  assert(!markup.includes('保存图片'), 'no album-saving action is exposed')
  assert(markup.includes('open-type="share"'), 'friend share uses the native mini-program share action')
  for (const icon of ['friend.svg', 'timeline.svg']) assert(markup.includes(icon), `missing official WeUI icon: ${icon}`)
  assert(markup.includes('data-action="timeline"'))
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, '../../app.json'), 'utf8'))
  assert(!JSON.stringify(appJson).includes('scope.writePhotosAlbum'), 'sharing requires no album permission')
  const footerCss = fs.readFileSync(path.join(__dirname, '../../app.wxss'), 'utf8')
  assert(footerCss.includes('margin: 42rpx 0 24rpx;'), 'legal footer spacing stays unchanged')
  console.log('stock share tests passed: current stock cover and empty default simulation, native paths, privacy, warm/cold initialization, races, cancel, failures and fallback')
}
run().catch(e => { console.error(e); process.exitCode = 1 })
