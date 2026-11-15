const assert = require('assert')
const fs = require('fs')
const path = require('path')
const { stockSaveSignature, readStockSaveLayout, paintStockSave, renderCurrentStockSave } = require('./stock-save')
const record = (section, kind, top, height, extras = {}) => ({ left: 14, top, width: 347, height,
  dataset: { exportSection: section, exportKind: kind }, ...extras })
function fixture(days) {
  const status = record('status', 'box', 100, 180, { 'margin-top': '12px' })
  const warnings = record('warnings', 'box', 300, 190, { 'margin-top': '10px' })
  const simulation = record('simulation', 'box', 650, days === 10 ? 650 : 350, { 'margin-top': '10px' })
  const sections = [status, warnings, simulation]
  const nodes = [...sections, record('status', 'text', 150, 25, { dataset: { exportSection: 'status', exportKind: 'text', exportText: '监管日期及剩余01天' } }),
    record('warnings', 'ring-label', 350, 22, { width: 24, 'font-size': '20px', 'line-height': '26px', 'white-space': 'nowrap', 'text-overflow': 'ellipsis',
      dataset: { exportSection: 'warnings', exportKind: 'ring-label', exportText: '4次' } }),
    ...Array.from({ length: days }, (_, i) => record('simulation', 'input', 700 + i * 22, 20, {
      width: 60, 'font-size': '12px', 'line-height': '20px', dataset: { exportSection: 'simulation', exportKind: 'input', exportText: i === days - 1 ? '-0.24' : i === 0 ? '5.38' : '' } })),
    record('simulation', 'text', 710 + days * 22, 20, { width: 80, dataset: { exportSection: 'simulation', exportKind: 'text', exportText: '已触发' } }),
    record('excluded', 'text', 500, 20, { dataset: { exportSection: 'excluded', exportKind: 'text', exportText: '广告、分享和隐私导航均排除' } }),
    record('simulation', 'text', 0, 0, { width: 0 })]
  return { sections, nodes }
}
async function run() {
  const drawn = [], canvas = { width: 0, height: 0 }
  const ctx = { font: '', scale() {}, translate() {}, fillRect() {}, save() {}, restore() {}, beginPath() {}, closePath() {},
    moveTo() {}, lineTo() {}, quadraticCurveTo() {}, fill() {}, stroke() {}, rect() {}, clip() {}, arc() {},
    fillText(value, x, y, maxWidth) { drawn.push({ value, font: this.font, color: this.fillStyle, x, y, maxWidth }) }, measureText(value) {
      const size = Number((this.font.match(/([\d.]+)px/) || [0, 12])[1])
      return { width: String(value).length * size * 1.2 }
    } }
  canvas.getContext = () => ctx
  let current = fixture(10), exported = 0
  const platform = {
    createSelectorQuery() { return { in() { return this }, select() { return this }, selectAll() { return this }, fields() { return this },
      exec(fn) { fn([{ left: 0, width: 375, height: 1300, top: 0, 'padding-left': '14px' }, current.sections, current.nodes, { node: canvas }]) } } },
    canvasToTempFilePath(options) { exported++; options.success({ tempFilePath: '/tmp/saved-current.png' }) }
  }
  const layout = await readStockSaveLayout({}, platform)
  assert.strictEqual(layout.height, 1068, 'three full sections, original margins, no ad gap')
  assert.strictEqual(layout.records.find(n => n.dataset.exportSection === 'status').y, 14)
  assert.strictEqual(layout.records.find(n => n.dataset.exportSection === 'simulation').y, 404)
  assert(!layout.records.some(n => n.dataset.exportSection === 'excluded' || n.width === 0))
  paintStockSave(ctx, layout)
  const drawnValues = drawn.map(item => item.value)
  assert(drawnValues.includes('5.38') && drawnValues.includes('-0.24') && drawnValues.includes('已触发'))
  assert(drawnValues.includes('4次'), 'ring count text stays complete instead of being ellipsized')
  assert(Number((drawn.find(item => item.value === '4次').font.match(/([\d.]+)px/) || [0, 30])[1]) < 20,
    'ring count font shrinks to fit its actual content box')
  assert(drawnValues.includes('监管日期及剩余01天'))
  assert(!drawnValues.some(t => String(t).includes('默认') || String(t).includes('以正式公告为准')))
  const fitted = { ...record('status', 'text', 120, 20, { width: 60, 'font-size': '15px', 'line-height': '20px', 'white-space': 'nowrap', 'text-overflow': 'ellipsis',
    dataset: { exportSection: 'status', exportKind: 'text', exportText: '九阳股份' } }) }
  paintStockSave(ctx, { width: 100, height: 40, background: '#FFF', records: [fitted] })
  assert(drawn.some(item => item.value === '九阳股份'), 'short native labels remain complete in the exported image')
  const textRecord = (value, extras) => ({ ...record('simulation', 'text', 0, 20), x: 10, y: 20,
    width: 60, 'font-size': '15px', 'line-height': '20px', color: '#185A96',
    dataset: { exportSection: 'simulation', exportKind: 'text', exportText: value }, ...extras })
  const typography = [
    textRecord('模拟计算', { width: 52, height: 18.5, 'font-size': '13px', 'font-weight': '600', 'line-height': 'normal' }),
    textRecord('+4.30', { width: 43.8, height: 18, 'font-weight': '650', color: '#1C1C1E', 'font-family': '-apple-system, "PingFang SC", sans-serif' }),
    textRecord('%', { x: 54.8, width: 10, height: 14, 'font-size': '10px', 'font-weight': '650' }),
    textRecord('10日计算', { width: 66, height: 27, 'padding-top': '4px', 'padding-bottom': '4px',
      'border-top-width': '1px', 'border-bottom-width': '1px', 'line-height': '15px' }),
    textRecord('这是一只名称很长的股票', { width: 30, 'white-space': 'nowrap', 'text-overflow': 'ellipsis' })
  ]
  paintStockSave(ctx, { width: 200, height: 100, records: typography })
  const title = drawn.find(item => item.value === '模拟计算')
  assert(title && title.font === 'bold 13px sans-serif', 'native single-line title is not wrapped or cut into 模拟计')
  assert.strictEqual(title.y, 29.25, 'normal line height is centred using the actual native content box')
  assert.strictEqual(title.maxWidth, 52)
  const numeric = drawn.find(item => item.value === '+4.30')
  assert.strictEqual(numeric.font, 'bold 15px sans-serif', '650 weight cannot silently fall back to a previous small canvas font')
  assert.strictEqual(numeric.color, '#1C1C1E', 'numbers retain the page colour')
  assert.strictEqual(numeric.maxWidth, 43.8, 'number stays inside its native box next to the percent unit')
  const unit = drawn.find(item => item.value === '%')
  assert.strictEqual(unit.font, 'bold 10px sans-serif')
  assert.strictEqual(unit.x, 54.8, 'unit retains its page position')
  assert.strictEqual(unit.color, '#185A96')
  assert(drawn.some(item => item.value.endsWith('…')), 'genuinely long names still use ellipsis')
  const arcs = [], corners = []
  const ringContext = { ...ctx, arc(...args) { arcs.push(args) }, quadraticCurveTo(...args) { corners.push(args) } }
  const ring = { x: 10, y: 20, left: 10, top: 20, width: 70, height: 70,
    'border-top-left-radius': '50%', 'background-color': '#E5E7EB',
    dataset: { exportSection: 'warnings', exportKind: 'ring', exportProgress: 25, exportColor: '#B9D6F2' } }
  const inner = { x: 19, y: 29, left: 19, top: 29, width: 52, height: 52,
    'border-top-left-radius': '50%', 'background-color': '#FFFFFF',
    dataset: { exportSection: 'warnings', exportKind: 'box' } }
  paintStockSave(ringContext, { width: 100, height: 110, records: [ring, inner] })
  assert.strictEqual(corners.length, 0, 'circle edges must never use the rounded-rectangle quadratic approximation')
  assert(arcs.some(([x, y, r, start, end]) => x === 45 && y === 55 && r === 35 && start === 0 && end === Math.PI * 2),
    'outer ring is an exact circle')
  assert(arcs.some(([x, y, r, start, end]) => x === 45 && y === 55 && r === 26 && start === 0 && end === Math.PI * 2),
    'inner opening is an exact concentric circle, giving a uniform ring thickness')
  const signatureData = { detail: { ts_code: '301313.SZ', name: '凡拓数创', currentPrice: '32.51', warnings: [] },
    visibleDays: 10, inputRows: [{ stock: '5.38', index: '-0.24', space: '2.78%' }] }
  const signature = stockSaveSignature(signatureData)
  signatureData.sharingStock = true
  assert.strictEqual(stockSaveSignature(signatureData), signature, 'progress UI is not a data change')
  signatureData.inputRows[0].stock = '9.99'
  assert.notStrictEqual(stockSaveSignature(signatureData), signature)
  const result = await renderCurrentStockSave({}, () => true, platform)
  assert.strictEqual(result.path, '/tmp/saved-current.png')
  assert.strictEqual(canvas.width, 1)
  assert.strictEqual(canvas.height, 1)
  const before = exported
  await assert.rejects(() => renderCurrentStockSave({}, () => false, platform), /页面已更新/)
  assert.strictEqual(exported, before)
  platform.canvasToTempFilePath = options => options.fail({ errMsg: 'export failed' })
  await assert.rejects(() => renderCurrentStockSave({}, () => true, platform), /图片生成失败/)
  assert.strictEqual(canvas.width, 1)
  current = fixture(2)
  const two = await readStockSaveLayout({}, platform)
  assert.strictEqual(two.height, 768)
  assert.strictEqual(two.records.filter(n => n.dataset.exportKind === 'input').length, 2)
  current.sections.pop()
  await assert.rejects(() => readStockSaveLayout({}, platform), /完整结果/)
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/stock/index.wxml'), 'utf8')
  for (const section of ['status', 'warnings', 'simulation']) assert(wxml.includes(`data-export-section="${section}"`))
  assert(wxml.includes('data-export-text="{{cell.value}}"'))
  assert(wxml.includes('data-export-kind="ring-label"'))
  const exportEnd = wxml.indexOf('class="stock-share-card card"')
  assert(exportEnd > wxml.lastIndexOf('data-export-kind='))
  console.log('current stock save tests passed: 2/10-day user inputs, live layout, section spacing, exclusion, signatures, stale/export failure cleanup')
}
run().catch(e => { console.error(e); process.exitCode = 1 })
