// Save the three rendered result sections using their actual layout and computed styles.
// Unlike the compact friend cover, this includes current simulation inputs and all visible days.
const { captureStockShare } = require('./stock-share')
const STYLE_FIELDS = [
  'background-color', 'color', 'font-size', 'font-weight', 'font-family', 'line-height',
  'text-align', 'white-space', 'text-overflow', 'overflow', 'visibility', 'opacity',
  'padding-top', 'padding-right', 'padding-bottom', 'padding-left', 'margin-top',
  ...['top', 'right', 'bottom', 'left'].flatMap(side => [`border-${side}-width`, `border-${side}-color`]),
  'border-top-left-radius'
]
const SECTIONS = ['status', 'warnings', 'simulation']
const number = value => Number.parseFloat(value) || 0
const shown = node => node && node.width > 0 && node.height > 0 && node.visibility !== 'hidden'

function stockSaveSignature(data) {
  const { generatedAt, ...display } = captureStockShare(data)
  return JSON.stringify({ display, monitoring: (data.detail || {}).monitoringSummaries || [],
    trading: (data.detail || {}).tradingBanner || null, visibleDays: data.visibleDays,
    rows: (data.inputRows || []).map(row => ({ day: row.day, date: row.date, stock: row.stock, index: row.index,
      deviation10: row.deviation10, deviation30: row.deviation30, space: row.space })) })
}

function readStockSaveLayout(page, platform) {
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      fn(value)
    }
    const timer = setTimeout(() => finish(reject, new Error('页面布局读取失败，请重试')), 2500)
    const read = () => {
      const query = platform.createSelectorQuery().in(page)
      const fields = { dataset: true, rect: true, size: true, computedStyle: STYLE_FIELDS }
      query.select('.stock-page').fields(fields)
      query.selectAll('.stock-export-section').fields(fields)
      query.selectAll('.stock-export-node').fields(fields)
      query.select('#stock-share-canvas').fields({ node: true })
      query.exec(result => {
        try {
          const [surface, sections, nodes, canvasResult] = result || []
          if (!shown(surface) || !Array.isArray(sections) || !Array.isArray(nodes) || !canvasResult || !canvasResult.node) {
            throw new Error('页面布局读取失败，请重试')
          }
          const groups = SECTIONS.map(key => sections.find(n => n.dataset && n.dataset.exportSection === key))
          if (groups.some(g => !shown(g))) throw new Error('请等待完整结果显示后再保存')
          // Remove search, ads, share actions and legal navigation; preserve each result section verbatim.
          const padding = number(surface['padding-left'])
          let height = padding
          const offsets = new Map()
          groups.forEach((g, i) => {
            if (i) height += number(g['margin-top'])
            offsets.set(g.dataset.exportSection, { x: -surface.left, y: height - g.top })
            height += g.height
          })
          height += padding
          const records = nodes.filter(shown).filter(n => offsets.has((n.dataset || {}).exportSection))
            .map(n => {
              const offset = offsets.get(n.dataset.exportSection)
              return { ...n, dataset: { ...n.dataset }, x: n.left + offset.x, y: n.top + offset.y }
            })
          if (!records.length) throw new Error('页面内容读取失败，请重试')
          finish(resolve, { width: surface.width, height, background: surface['background-color'], records, canvas: canvasResult.node })
        } catch (error) { finish(reject, error) }
      })
    }
    try { read() } catch (error) { finish(reject, error) }
  })
}

function radiusFor(node) {
  const value = String(node['border-top-left-radius'] || '')
  return Math.min(value.includes('%') ? Math.min(node.width, node.height) * number(value) / 100 : number(value),
    node.width / 2, node.height / 2)
}
function roundedPath(ctx, x, y, w, h, radius) {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2))
  // A circle is not four quadratic rounded corners: those bulge outward,
  // leaving a circular outer ring with a visibly distorted inner opening.
  // Use the same exact circular geometry for its background and clipping.
  if (Math.abs(w - h) <= .5 && r >= Math.min(w, h) / 2 - .01) {
    ctx.beginPath(); ctx.arc(x + w / 2, y + h / 2, Math.min(w, h) / 2, 0, Math.PI * 2)
    ctx.closePath()
    return
  }
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r); ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h); ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r); ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath()
}
function backgroundAndBorder(ctx, n) {
  const r = radiusFor(n), fill = n['background-color']
  if (fill && !['transparent', 'rgba(0, 0, 0, 0)'].includes(fill)) {
    roundedPath(ctx, n.x, n.y, n.width, n.height, r); ctx.fillStyle = fill; ctx.fill()
  }
  const widths = ['top', 'right', 'bottom', 'left'].map(side => number(n[`border-${side}-width`]))
  if (widths.every(w => w === widths[0]) && widths[0] > 0) {
    const half = widths[0] / 2
    roundedPath(ctx, n.x + half, n.y + half, n.width - widths[0], n.height - widths[0], Math.max(0, r - half))
    ctx.lineWidth = widths[0]; ctx.strokeStyle = n['border-top-color']; ctx.stroke()
  } else {
    const edges = [[n.x, n.y, n.x + n.width, n.y], [n.x + n.width, n.y, n.x + n.width, n.y + n.height],
      [n.x, n.y + n.height, n.x + n.width, n.y + n.height], [n.x, n.y, n.x, n.y + n.height]]
    widths.forEach((w, i) => { if (w) {
      ctx.lineWidth = w; ctx.strokeStyle = n[`border-${['top', 'right', 'bottom', 'left'][i]}-color`]
      ctx.beginPath(); ctx.moveTo(edges[i][0], edges[i][1]); ctx.lineTo(edges[i][2], edges[i][3]); ctx.stroke()
    } })
  }
}
function canvasFont(node, size) {
  // Native mobile canvas parsers do not consistently accept CSS variable
  // weights (650) or browser-only system font stacks. An invalid assignment
  // silently leaves the previous font in place, changing both size and spacing.
  const weight = String(node['font-weight'] || '')
  const bold = weight === 'bold' || weight === 'bolder' || number(weight) >= 600
  return `${bold ? 'bold' : 'normal'} ${size}px sans-serif`
}
function paintText(ctx, n) {
  const value = String(n.dataset.exportText == null ? '' : n.dataset.exportText)
  if (!value) return
  let size = number(n['font-size']) || 12
  let lineHeight = number(n['line-height']) || size * 1.2
  if (n.dataset.exportKind === 'ring-label') {
    const available = Math.max(1, n.width - number(n['padding-left']) - number(n['padding-right']))
    while (size > 8) {
      ctx.font = canvasFont(n, size)
      if (ctx.measureText(value).width <= available) break
      size -= 1
    }
    lineHeight = Math.min(lineHeight, size * 1.2)
  }
  ctx.font = canvasFont(n, size)
  ctx.fillStyle = n.color || '#1C1C1E'; ctx.textBaseline = 'middle'
  const left = number(n['padding-left']) + number(n['border-left-width'])
  const right = number(n['padding-right']) + number(n['border-right-width'])
  const insetTop = number(n['padding-top']) + number(n['border-top-width'])
  const insetBottom = number(n['padding-bottom']) + number(n['border-bottom-width'])
  const height = Math.max(0, n.height - insetTop - insetBottom)
  const width = Math.max(0, n.width - left - right)
  const align = n['text-align'] || 'left'
  ctx.textAlign = align
  const x = align === 'center' ? n.x + left + width / 2 : align === 'right' || align === 'end' ? n.x + n.width - right : n.x + left
  // The native query sometimes omits inherited white-space. A one-line
  // content box must stay one line, even when canvas font metrics differ.
  const nowrap = n['white-space'] === 'nowrap' || n.dataset.exportKind === 'input' ||
    n.dataset.exportKind === 'ring-label' || (!value.includes('\n') && height < lineHeight * 1.65)
  const lines = []
  value.split('\n').forEach(paragraph => {
    if (nowrap) { lines.push(paragraph); return }
    let line = ''
    Array.from(paragraph).forEach(char => {
      if (line && ctx.measureText(line + char).width > width) { lines.push(line); line = char } else line += char
    })
    lines.push(line)
  })
  // The native page and the export canvas can use slightly different font
  // metrics. Keep short labels intact (for example “九阳股份” and
  // “模拟计算”) instead of adding an ellipsis merely because the canvas
  // measured them a few pixels wider. Truly long labels still follow the
  // page's ellipsis behavior.
  let scaleX = 1
  if (nowrap && n.dataset.exportKind !== 'ring-label' && n['text-overflow'] === 'ellipsis') {
    const measured = ctx.measureText(lines[0]).width
    if (measured > width) {
      const fit = width / measured
      if (fit >= .82) scaleX = fit
      else {
        let chars = Array.from(lines[0])
        while (chars.length && ctx.measureText(chars.join('') + '…').width > width) chars.pop()
        lines[0] = chars.join('') + '…'
      }
    }
  }
  ctx.save(); ctx.beginPath(); ctx.rect(n.x, n.y, n.width, n.height); ctx.clip()
  const top = n.y + insetTop + (nowrap ? height / 2 : lineHeight / 2)
  lines.forEach((line, i) => {
    // Single-line text without ellipsis uses its native box as the maximum
    // width. This keeps titles/values intact without drawing over their unit
    // or neighbouring column when platform font widths differ slightly.
    if (scaleX === 1) return nowrap && n['text-overflow'] !== 'ellipsis'
      ? ctx.fillText(line, x, top + i * lineHeight, width)
      : ctx.fillText(line, x, top + i * lineHeight)
    const y = top + i * lineHeight
    ctx.save(); ctx.translate(x * (1 - scaleX), 0); ctx.scale(scaleX, 1)
    ctx.fillText(line, x, y); ctx.restore()
  })
  ctx.restore()
}
function paintStockSave(ctx, layout, images = new Map()) {
  ctx.fillStyle = layout.background && layout.background !== 'rgba(0, 0, 0, 0)' ? layout.background : '#F2F3F5'
  ctx.fillRect(0, 0, layout.width, layout.height)
  const clips = []
  layout.records.forEach(n => {
    ctx.save()
    // Native overflow clips (notably rounded simulation tables) also apply to descendants.
    clips.filter(c => n.left >= c.left && n.top >= c.top && n.left + n.width <= c.left + c.width + 1 &&
      n.top + n.height <= c.top + c.height + 1 && n.dataset.exportSection === c.dataset.exportSection)
      .forEach(c => { roundedPath(ctx, c.x, c.y, c.width, c.height, radiusFor(c)); ctx.clip() })
    ctx.globalAlpha = n.opacity == null ? 1 : Number(n.opacity)
    backgroundAndBorder(ctx, n)
    if (n.dataset.exportKind === 'ring') {
      const r = Math.min(n.width, n.height) / 2, x = n.x + n.width / 2, y = n.y + n.height / 2
      const progress = Math.min(100, Math.max(0, Number(n.dataset.exportProgress) || 0))
      ctx.fillStyle = '#E5E7EB'; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
      if (progress) {
        ctx.fillStyle = n.dataset.exportColor
        ctx.beginPath(); ctx.moveTo(x, y)
        ctx.arc(x, y, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * progress / 100)
        ctx.closePath(); ctx.fill()
      }
    } else if (n.dataset.exportKind === 'image') {
      const image = images.get(n.dataset.exportSrc)
      if (image) ctx.drawImage(image, n.x, n.y, n.width, n.height)
    } else if (['text', 'input', 'ring-label'].includes(n.dataset.exportKind)) paintText(ctx, n)
    ctx.restore()
    if (n.overflow === 'hidden' && n.dataset.exportKind !== 'text' && n.dataset.exportKind !== 'input') clips.push(n)
  })
}
function loadImages(canvas, records) {
  const sources = [...new Set(records.filter(n => n.dataset.exportKind === 'image').map(n => n.dataset.exportSrc))]
  return Promise.all(sources.map(source => new Promise((resolve, reject) => {
    const image = canvas.createImage()
    image.onload = () => resolve([source, image])
    image.onerror = () => reject(new Error('页面图标读取失败，请重试'))
    image.src = source === '/assets/clear-history.svg' ? '/assets/export-clear.png' : source
  }))).then(pairs => new Map(pairs))
}
async function renderCurrentStockSave(page, isCurrent, platform = wx) {
  const layout = await readStockSaveLayout(page, platform)
  if (!isCurrent()) throw new Error('页面已更新，请重新保存')
  const canvas = layout.canvas
  try {
    const images = await loadImages(canvas, layout.records)
    if (!isCurrent()) throw new Error('页面已更新，请重新保存')
    const scale = Math.min(2, 4096 / layout.height, 4096 / layout.width)
    canvas.width = Math.ceil(layout.width * scale); canvas.height = Math.ceil(layout.height * scale)
    const ctx = canvas.getContext('2d'); ctx.scale(scale, scale)
    paintStockSave(ctx, layout, images)
    return await new Promise((resolve, reject) => {
      platform.canvasToTempFilePath({ canvas, x: 0, y: 0, width: canvas.width, height: canvas.height,
        destWidth: canvas.width, destHeight: canvas.height, fileType: 'png',
        success: result => resolve({ path: result.tempFilePath }), fail: () => reject(new Error('图片生成失败，请重试')) }, page)
    })
  } finally { canvas.width = 1; canvas.height = 1 }
}
module.exports = { stockSaveSignature, readStockSaveLayout, paintStockSave, renderCurrentStockSave }
