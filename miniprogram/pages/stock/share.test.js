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
const ctx = { ...measure, fillRect() {}, strokeRect() {}, beginPath() {}, arc() {}, stroke() {}, fill() {},
  moveTo() {}, lineTo() {}, quadraticCurveTo() {}, closePath() {},
  save() {}, restore() {}, rect() {}, clip() {}, scale() {}, drawImage() {},
  fillText(s) { drawLog.push(s) } }
let exportOptions, saveCalls, previews, imageMenus, toasts, menus, exportMode, selectedCanvas, saveError
function resetPlatform() {
  saveCalls = []; previews = []; imageMenus = []; toasts = []; menus = []; exportMode = 'success'; saveError = null
  global.wx = {
    getStorageSync: () => [], setStorageSync() {}, showToast: v => toasts.push(v.title),
    showShareMenu: v => menus.push(v.menus),
    createSelectorQuery: () => {
      let page, layout = false
      return { in(value) { page = value; return this }, select(selector) { selectedCanvas = selector; return this },
        selectAll() { layout = true; return this }, fields() { return this },
        exec(fn) {
          if (!layout) { fn([{ node: canvas }]); return }
          const section = (key, top, height) => ({ left: 14, top, width: 347, height, dataset: { exportSection: key }, 'margin-top': '10px' })
          fn([{ left: 0, top: 0, width: 375, height: 1200, 'padding-left': '14px' },
            [section('status', 100, 120), section('warnings', 240, 180), section('simulation', 440, 600)],
            page.data.inputRows.slice(0, page.data.visibleDays).map((row, i) => ({ left: 80, top: 480 + i * 20, width: 60, height: 20,
              'font-size': '12px', 'line-height': '20px', dataset: { exportSection: 'simulation', exportKind: 'input', exportText: row.stock } })),
            { node: canvas }])
        } }
    },
    canvasToTempFilePath(o) { if (selectedCanvas === '#stock-share-cover-canvas') { o.success({ tempFilePath: '/tmp/current-stock-cover.png' }); return }; exportOptions = o; if (exportMode === 'success') o.success({ tempFilePath: '/tmp/result.png' });
      else if (exportMode === 'fail') o.fail({ errMsg: 'export failed' }) },
    showShareImageMenu(o) { imageMenus.push(o); o.success({}) },
    saveImageToPhotosAlbum(o) { saveCalls.push(o); if (saveError) o.fail(saveError); else o.success({}) },
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
  custom.warnings.forEach(w => assert(cover.commands.some(c => c.value === w.number)))
  assert(!cover.commands.some(c => c.type === 'text' && c.value.includes('模拟')))
  const header = cover.commands.filter(c => c.type === 'text' && c.y < 80)
  assert.deepStrictEqual(header.map(c => c.value), [custom.name, custom.symbol, custom.price, custom.change])
  assert.strictEqual(new Set(header.map(c => c.y + c.size)).size, 1, 'all four quote fields share one row')
  const conclusion = cover.commands.find(c => c.type === 'text' && c.value === custom.status)
  assert(conclusion && conclusion.y > Math.max(...header.map(c => c.y + c.size)) && conclusion.y < 152)
  const coverRings = cover.commands.filter(c => c.type === 'ring')
  assert.strictEqual(coverRings.length, 4)
  assert.strictEqual(cover.commands[0].fill, '#F2F3F5', 'cover uses the stock page background')
  assert.strictEqual(cover.commands.filter(c => c.type === 'box' && c.w === 337 && c.radius === 12).length, 4)
  assert.strictEqual(cover.commands.filter(c => c.type === 'circle' && c.fill === '#FFFFFF').length, 4)
  const coloredCover = share.createShareCoverPlan({ ...custom,
    warnings: custom.warnings.map((w, i) => ({ ...w, tone: i === 0 ? 'warning' : 'severe', progress: 100 })) }, measure)
  assert(coloredCover.commands.some(c => c.type === 'box' && c.w === 337 && c.fill === '#FFFAE8'))
  assert(coloredCover.commands.some(c => c.type === 'box' && c.w === 337 && c.fill === '#FFF5F5'))
  coloredCover.commands.filter(c => c.type === 'text' && custom.warnings.some(w => w.number === c.value))
    .forEach(c => assert.strictEqual(c.color, '#1C1C1E', 'metric values retain the stock page black text'))
  const fullRingArcs = []
  share.paintSharePlan({ ...ctx, arc(...args) { fullRingArcs.push(args) } }, coloredCover)
  assert(fullRingArcs.filter(a => a[2] === 61).every(a => a[3] === 0 && a[4] === Math.PI * 2), '100% uses an explicit full circle')

  const coverArcs = [], fullArcs = []
  share.paintSharePlan({ ...ctx, arc(...args) { coverArcs.push(args) } }, cover)
  share.paintSharePlan({ ...ctx, arc(...args) { fullArcs.push(args) } }, plan)
  assert(coverArcs.length >= 4 && coverArcs.every(a => [61, 52].includes(a[2])), 'compact cover rings fit the warning cards')
  assert(fullArcs.length >= 4 && fullArcs.every(a => a[2] === 49), 'full saved-result ring size stays unchanged')

  custom.warnings.forEach((w, i) => {
    assert.strictEqual(coverRings[i].progress, w.progress)
    assert.strictEqual(coverRings[i].color, w.color)
    assert(cover.commands.some(c => c.type === 'text' && c.value === w.ring && c.align === 'center'))
  })
  const triggeredCover = share.createShareCoverPlan({ ...custom, status: '当日已触发严重异动',
    statusClass: 'current-triggered', summaryTone: 'risk' }, measure)
  assert.strictEqual(triggeredCover.commands.find(c => c.value === '当日已触发严重异动').color, '#C0271E')
  const unavailableCover = share.createShareCoverPlan({ ...custom, status: '未知状态', summaryTone: 'unknown',
    warnings: custom.warnings.map(w => ({ ...w, value: '待核验', number: '待核验', unit: '', ring: '—', progress: 0, color: '#999999', tone: 'unknown' })) }, measure)
  assert(unavailableCover.commands.filter(c => c.type === 'ring').every(c => c.progress === 0))
  assert.strictEqual(unavailableCover.commands.find(c => c.value === '未知状态').color, '#8A6D0B')
  const longNameCover = share.createShareCoverPlan({ ...custom, name: '这是一只名称特别长的示例股票用来检验单行布局' }, measure)
  assert(longNameCover.commands.find(c => c.type === 'text').value.endsWith('…'))
  assert.strictEqual(longNameCover.commands.filter(c => c.type === 'text' && c.y < 80).length, 4)

  p.data.inputRows[0].stock = '99'
  assert.strictEqual(JSON.stringify(custom), customSerialized, 'later edits cannot mutate a captured image')
  assert.strictEqual(saveCalls.length, 0, 'opening, editing and metadata capture never request album access')
  const countBefore = [reads, quotes]
  await p.saveStockPage()
  assert.deepStrictEqual([reads, quotes], countBefore, 'sharing never requests S1 or S2')
  assert.strictEqual(p.data.visibleDays, 10)
  assert.strictEqual(p.data.inputRows[0].stock, '99')
  assert(drawLog.includes('99'), 'saving renders the current custom input rather than a default two-day image')
  assert.strictEqual(canvas.width, 1, 'release canvas buffer after export')
  assert.strictEqual(saveCalls.length, 1)
  assert.strictEqual(saveCalls[0].filePath, '/tmp/result.png')
  assert.strictEqual(toasts.at(-1), '已保存到相册')
  assert.strictEqual(imageMenus.length, 0, 'save does not open a combined share menu')
  assert.strictEqual(previews.length, 0, 'save does not fall back to preview')
  assert.strictEqual(p.data.sharingStock, false)
  const route = p.onShareAppMessage()
  const timeline = p.onShareTimeline()
  assert.strictEqual(route.path, '/' + entry.shareEntry(custom.code))
  assert.strictEqual(timeline.query, route.path.split('?')[1])
  assert(!Object.hasOwn(route, 'title'), 'custom stock-name/title is removed; native title area belongs to WeChat')
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
  const first = p.saveStockPage(); await tick()
  const duplicate = p.saveStockPage(); await duplicate
  assert.strictEqual(p.data.sharingStock, true)
  exportOptions.success({ tempFilePath: '/tmp/duplicate.png' }); await first
  assert.strictEqual(saveCalls.length, 2, 'double tapping produces only one more save')
  const beforeRace = saveCalls.length
  const raced = p.saveStockPage(); await tick()
  await p.chooseStock({ ts_code: '600825.SH', name: '新华传媒' })
  exportOptions.success({ tempFilePath: '/tmp/old-stock.png' }); await raced
  assert.strictEqual(saveCalls.length, beforeRace, 'late export cannot share the wrong selected stock')
  exportMode = 'fail'; await p.saveStockPage()
  assert.strictEqual(p.data.sharingStock, false)
  assert(toasts.some(t => t.includes('失败')))
  exportMode = 'success'
  for (const [error, expected] of [
    [{ errMsg: 'saveImageToPhotosAlbum:fail auth deny' }, '未获保存授权'],
    [{ errMsg: 'saveImageToPhotosAlbum:fail privacy permission denied', errno: 103 }, '未获保存授权'],
    [{ errMsg: 'saveImageToPhotosAlbum:fail cancel' }, '已取消'],
    [{ errMsg: 'api scope is not declared in the privacy agreement', errno: 112 }, '保存功能暂不可用'],
    [{ errMsg: 'appid privacy api banned' }, '保存功能暂不可用'],
    [{ errMsg: 'disk full' }, '图片保存失败']
  ]) {
    saveError = error
    await p.saveStockPage()
    assert(toasts.at(-1).includes(expected), `unexpected save failure: ${toasts.at(-1)}`)
    assert.strictEqual(p.data.sharingStock, false, 'failure always releases the save button')
  }
  saveError = null
  const nativeSave = wx.saveImageToPhotosAlbum
  delete wx.saveImageToPhotosAlbum
  await p.saveStockPage()
  assert(toasts.at(-1).includes('不支持保存图片'))
  assert.strictEqual(p.data.sharingStock, false)
  wx.saveImageToPhotosAlbum = nativeSave
  await p.saveStockPage()
  assert.strictEqual(toasts.at(-1), '已保存到相册', 'successful retry is possible after refusal/failure')
  assert.strictEqual(previews.length, 0)
  assert.strictEqual(imageMenus.length, 0)
  const beforeFriend = saveCalls.length
  p.onShareAppMessage()
  assert.strictEqual(saveCalls.length, beforeFriend, 'friend sharing never requests album authorization')

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
  let late = lifetime.saveStockPage(); await tick()
  lifetime.resetSimulationInputs()
  exportOptions.success({ tempFilePath: '/tmp/old-session.png' }); await late
  assert.strictEqual(saveCalls.length, 0, 'reinitializing the same stock invalidates a late image')
  late = lifetime.saveStockPage(); await tick()
  const NativeDate = Date, nextDay = NativeDate.now() + 86400000
  global.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [nextDay])) }
    static now() { return nextDay }
  }
  try {
    exportOptions.success({ tempFilePath: '/tmp/yesterday.png' }); await late
    assert.strictEqual(saveCalls.length, 0, 'cross-date export cannot open yesterday\'s image')
  } finally { global.Date = NativeDate }
  late = lifetime.saveStockPage(); await tick()
  lifetime.onUnload()
  exportOptions.success({ tempFilePath: '/tmp/closed-page.png' }); await late
  assert.strictEqual(saveCalls.length, 0, 'unloaded pages cannot save an image')

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
  assert(markup.includes('分享一下') && markup.includes('保存当前股票状态与异动计算结果'))
  assert(markup.includes('stock-share-card card'))
  assert(markup.includes('class="share-actions"'), 'share buttons use the horizontal action row')
  const stockCss = fs.readFileSync(path.join(__dirname, 'index.wxss'), 'utf8')
  for (const style of ['#78A8F2', '#6597E3', '#D6DFEF', 'height: 76rpx', 'gap: 20rpx']) assert(stockCss.includes(style), `missing share styling: ${style}`)
  assert(stockCss.includes('share-btn--ghost { border: 1rpx solid #D6DFEF; background: #FFFFFF; color: #3A6FD8;'))
  const saveIcon = fs.readFileSync(path.join(__dirname, '../../assets/weui-share/save.svg'), 'utf8')
  assert(saveIcon.includes('fill="#3A6FD8"'), 'save icon matches the button text color')
  for (const channel of ['发给朋友', '保存图片']) assert(markup.includes(channel), `missing share channel: ${channel}`)
  assert(!markup.includes('炫朋友圈'), 'no custom Moments entry remains')
  assert(markup.includes('open-type="share"'), 'friend share uses the native mini-program share action')
  for (const icon of ['friend.svg', 'save.svg']) assert(markup.includes(icon), `missing official WeUI icon: ${icon}`)
  assert(markup.includes('bindtap="saveStockPage"'))
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, '../../app.json'), 'utf8'))
  assert(!JSON.stringify(appJson).includes('scope.writePhotosAlbum'), 'app.json does not support an album permission description; native API handles the scope')
  const privacy = fs.readFileSync(path.join(__dirname, '../privacy/index.wxml'), 'utf8')
  for (const description of ['保存图片', '相册写入', '不读取', '模拟输入信息不上传', '不建立用户画像']) assert(privacy.includes(description))
  const footerCss = fs.readFileSync(path.join(__dirname, '../../app.wxss'), 'utf8')
  assert(footerCss.includes('margin: 42rpx 0 24rpx;'), 'legal footer spacing stays unchanged')
  console.log('stock share tests passed: current stock cover and empty default simulation, native paths, privacy, warm/cold initialization, races, direct saving, permission refusal, cancel, configuration failures and retry')
}
run().catch(e => { console.error(e); process.exitCode = 1 })
