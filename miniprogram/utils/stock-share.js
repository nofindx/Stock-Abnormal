// Local-only rendering of displayed results. No market requests, persistent inputs or ads.
const { stockCode, shareEntry } = require('./share-entry')
const WIDTH = 750
const LIMIT = 4096
const COLORS = { ink: '#1C1C1E', muted: '#636366', base: '#185A96', severe: '#C83A36', warning: '#8A6D0B' }
const text = value => value == null ? '' : String(value)

function captureStockShare(data, defaultRows = [], now = new Date()) {
  const d = data.detail
  const code = stockCode(d && (d.ts_code || (data.selected || {}).ts_code))
  if (!d || !code || data.detailLoading) throw new Error('请等待股票详情加载完成')
  const monitor = d.monitoringSummary
  return {
    code, name: text(d.name), symbol: text(d.symbol || code.split('.')[0]),
    price: text(d.currentPrice == null || d.currentPrice === '' ? '--' : d.currentPrice), change: text(d.change || '--'),
    status: text(d.summaryStatus), statusClass: text(d.statusClass), summaryTone: text(d.summaryTone),
    quoteTime: text(data.dataUpdatedAt || d.tradeDate), generatedAt: now.toISOString(),
    forecast: [data.nextDayForecastLine, data.nextDayForecastText, data.nextDayForecastNotice,
      d.currentTriggered ? d.futureForecastLine : '', d.currentTriggered ? d.futureForecastText : ''].filter(Boolean).map(text),
    monitoring: monitor ? { badge: text(monitor.badgeText), dates: text(monitor.dateText),
      remaining: text(monitor.remainingText), rule: text(monitor.ruleText) } : null,
    dataNotice: text(data.detailError),
    warnings: (d.warnings || []).map(w => ({ title: text(w.title), value: text(w.currentText || '--'),
      target: text(w.target), remaining: text(w.remainingText), ring: text(w.ringText),
      progress: Math.min(100, Math.max(0, Number(w.progress) || 0)), tone: text(w.labelTone), color: text(w.toneColor) })),
    simulationReady: Boolean(data.simulationReady),
    simulationNotice: text((d.simulationApplicability || {}).message),
    rows: defaultRows.slice(0, 2).map(row => ({
      day: text(row.day), date: text(row.date), stock: '', index: '',
      deviation10: text(row.deviation10), deviation30: text(row.deviation30), space: text(row.space)
    }))
  }
}

function shareMetadata(data) {
  const d = data.detail || {}
  const code = stockCode(d.ts_code || (data.selected || {}).ts_code)
  return { title: code ? `${text(d.name) || code.split('.')[0]}｜异动计算` : '异动计算',
    path: '/' + shareEntry(code), query: code ? shareEntry(code).split('?')[1] : '',
    // Explicit fallback avoids platform screenshots containing local search history.
    imageUrl: '/assets/share-stock.png' }
}

function splitLines(value, width, measure) {
  const lines = []
  text(value).split('\n').forEach(paragraph => {
    let line = ''
    Array.from(paragraph).forEach(char => {
      if (line && measure(line + char) > width) { lines.push(line); line = char } else line += char
    })
    lines.push(line)
  })
  return lines
}

function createSharePlan(snapshot, ctx) {
  const commands = []
  const box = (x, y, w, h, fill = '#FFFFFF', stroke = '#E5E7EB') => commands.push({ type: 'box', x, y, w, h, fill, stroke })
  const label = (value, x, y, size = 22, color = COLORS.ink, weight = 'normal', align = 'left') => {
    commands.push({ type: 'text', value: text(value), x, y, size, color, weight, align })
  }
  const lines = (value, width, size = 22, weight = 'normal') => {
    ctx.font = `${weight} ${size}px sans-serif`
    return splitLines(value, width, s => ctx.measureText(s).width)
  }
  const paragraph = (value, x, y, width, size = 22, color = COLORS.ink, weight = 'normal') => {
    const wrapped = lines(value, width, size, weight)
    wrapped.forEach((line, i) => label(line, x, y + i * (size + 12), size, color, weight))
    return wrapped.length * (size + 12)
  }
  box(0, 0, WIDTH, LIMIT, '#FFFFFF', '#FFFFFF')
  let y = 24
  const start = commands.length
  let bodyY = y + 24
  bodyY += paragraph(`${snapshot.name}  ${snapshot.symbol}`, 48, bodyY, 650, 32, COLORS.ink, 'bold') + 6
  bodyY += paragraph(`${snapshot.price}  ${snapshot.change}`, 48, bodyY, 650, 30,
    snapshot.change.startsWith('-') ? '#16835D' : snapshot.change.startsWith('+') ? '#D94A45' : COLORS.ink, 'bold') + 8
  bodyY += paragraph(snapshot.status || '状态暂不可用', 48, bodyY, 650, 24,
    ['severe', 'monitoring', 'risk'].includes(snapshot.statusClass) ? COLORS.severe : snapshot.summaryTone === 'warning' ? COLORS.warning : COLORS.base, 'bold')
  snapshot.forecast.forEach(line => {
    // Keep the factual conclusion; the footer carries the source caveat once.
    const compact = line.replace(/[，,；;。\s]*具体以官方公告为准[。]?/g, '').trim()
    if (compact) bodyY += paragraph(compact, 48, bodyY, 650, 22, line.includes('以官方公告为准') ? COLORS.severe : COLORS.ink)
  })
  if (snapshot.monitoring) {
    const m = snapshot.monitoring
    bodyY += 10
    for (const line of [`监管日期：${m.dates} · 剩余 ${m.remaining} 天`, `监管原因：${m.rule}`]) {
      bodyY += paragraph(line, 48, bodyY, 650, 22, COLORS.severe)
    }
  }
  bodyY += 8
  if (snapshot.dataNotice) bodyY += paragraph(snapshot.dataNotice, 48, bodyY, 650, 20, COLORS.severe)
  const severe = ['severe', 'monitoring', 'risk'].includes(snapshot.statusClass)
  commands.splice(start, 0, { type: 'box', x: 24, y, w: 702, h: bodyY - y + 22,
    fill: '#FFFFFF', stroke: '#E5E7EB' })
  if (severe || snapshot.summaryTone === 'warning') commands.splice(start + 1, 0, {
    type: 'box', x: 24, y: y + 20, w: 4, h: bodyY - y - 20,
    fill: severe ? COLORS.severe : COLORS.warning, stroke: severe ? COLORS.severe : COLORS.warning
  })
  y = bodyY + 48
  label('四项预警', 24, y, 26, COLORS.ink, 'bold'); y += 44
  const warnings = snapshot.warnings.length ? snapshot.warnings : [{ title: '四项预警暂不可用', value: '数据不足' }]
  for (let i = 0; i < warnings.length; i += 2) {
    const pair = warnings.slice(i, i + 2)
    const height = Math.max(190, ...pair.map(w => 112 + lines(w.target || '', 198, 19).length * 29))
    pair.forEach((w, j) => {
      const x = 24 + j * 360
      const color = w.tone === 'severe' ? COLORS.severe : w.tone === 'warning' ? COLORS.warning : COLORS.ink
      box(x, y, 342, height)
      label(w.title, x + 18, y + 20, 24, color, 'bold')
      paragraph(w.value, x + 18, y + 60, 196, 29, color, 'bold')
      paragraph(w.target || '', x + 18, y + 112, 198, 19, COLORS.muted)
      commands.push({ type: 'ring', x: x + 273, y: y + 96, progress: w.progress || 0, color: w.color || '#B9D6F2' })
      const ringLines = lines(w.ring || '—', 96, 19)
      ringLines.forEach((line, n) => label(line, x + 273, y + 96 - ringLines.length * 14 + n * 28, 19, COLORS.ink, 'normal', 'center'))
    })
    y += height + 18
  }
  y += 8
  label('模拟计算（假设涨幅）', 24, y, 26, COLORS.ink, 'bold'); y += 42
  if (!snapshot.simulationReady) {
    y += paragraph(snapshot.simulationNotice || '模拟数据暂不可用', 24, y, 702, 22, COLORS.muted) + 18
  } else {
    const fields = [['stock', '股票涨幅'], ['index', '指数涨幅'], ['deviation10', '10日偏离'], ['deviation30', '30日偏离'], ['space', '距最近阈值']]
    for (let startRow = 0; startRow < snapshot.rows.length; startRow += 5) {
      const rows = snapshot.rows.slice(startRow, startRow + 5)
      const cellWidth = 566 / rows.length
      box(24, y, 702, 68, '#F7F8FA')
      label('指标', 40, y + 22, 22)
      rows.forEach((row, n) => {
        label(row.day, 160 + cellWidth * (n + 0.5), y + 8, 22, COLORS.ink, 'bold', 'center')
        label(row.date, 160 + cellWidth * (n + 0.5), y + 38, 18, COLORS.muted, 'normal', 'center')
      })
      y += 68
      fields.forEach(([key, title]) => {
        const cells = rows.map(row => {
          const value = key === 'stock' || key === 'index' ? '—' : row[key] || '--'
          return lines(value, cellWidth - 12, 21)
        })
        const height = Math.max(60, Math.max(...cells.map(c => c.length)) * 30 + 20)
        box(24, y, 702, height)
        label(title, 40, y + (height - 22) / 2, 21)
        cells.forEach((values, n) => values.forEach((value, line) => label(value, 160 + cellWidth * (n + 0.5),
          y + (height - values.length * 30) / 2 + line * 30, 21, value === '已触发' ? COLORS.severe : COLORS.ink, 'normal', 'center')))
        y += height
      })
      y += 20
    }
  }
  y += paragraph(`行情时间：${snapshot.quoteTime || '暂不可用'} · 以正式公告为准`, 24, y + 6, 702, 20, COLORS.muted) + 30
  if (y > LIMIT) throw new Error('分享内容过长，请缩短输入后重试')
  commands[0].h = y
  return { width: WIDTH, height: Math.ceil(y), commands }
}

function paintSharePlan(ctx, plan) {
  ctx.textBaseline = 'top'
  plan.commands.forEach(c => {
    if (c.type === 'box') {
      ctx.fillStyle = c.fill; ctx.fillRect(c.x, c.y, c.w, c.h)
      ctx.strokeStyle = c.stroke; ctx.lineWidth = 1; ctx.strokeRect(c.x, c.y, c.w, c.h)
    } else if (c.type === 'text') {
      ctx.font = `${c.weight} ${c.size}px sans-serif`; ctx.fillStyle = c.color; ctx.textAlign = c.align
      ctx.fillText(c.value, c.x, c.y)
    } else if (c.type === 'ring') {
      ctx.lineWidth = 12; ctx.strokeStyle = '#E5E7EB'
      ctx.beginPath(); ctx.arc(c.x, c.y, 49, 0, Math.PI * 2); ctx.stroke()
      if (c.progress > 0) {
        ctx.strokeStyle = c.color; ctx.beginPath()
        ctx.arc(c.x, c.y, 49, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * c.progress / 100); ctx.stroke()
      }
    }
  })
}

function createShareCoverPlan(snapshot, ctx) {
  const commands = [{ type: 'box', x: 0, y: 0, w: 750, h: 600, fill: '#FFFFFF', stroke: '#FFFFFF' }]
  const label = (value, x, y, size, color = COLORS.ink, weight = 'normal') =>
    commands.push({ type: 'text', value, x, y, size, color, weight, align: 'left' })
  const fit = (value, width, size) => {
    ctx.font = 'normal ' + size + 'px sans-serif'
    const chars = Array.from(text(value))
    const originalLength = chars.length
    while (chars.length && ctx.measureText(chars.join('') + (chars.length < originalLength ? '…' : '')).width > width) chars.pop()
    return chars.join('') + (chars.length < originalLength ? '…' : '')
  }
  label(fit(snapshot.name + '  ' + snapshot.symbol, 654, 32), 40, 30, 32, COLORS.ink, 'bold')
  label(snapshot.price + '  ' + snapshot.change, 40, 82, 30,
    snapshot.change.startsWith('-') ? '#16835D' : snapshot.change.startsWith('+') ? COLORS.severe : COLORS.ink, 'bold')
  label(fit(snapshot.status || '状态暂不可用', 654, 24), 40, 132, 24,
    ['severe', 'monitoring', 'risk'].includes(snapshot.statusClass) ? COLORS.severe :
      snapshot.summaryTone === 'warning' ? COLORS.warning : COLORS.base)
  snapshot.warnings.slice(0, 4).forEach((w, i) => {
    const x = 40 + i % 2 * 344, y = 190 + Math.floor(i / 2) * 158
    const color = w.tone === 'severe' ? COLORS.severe : w.tone === 'warning' ? COLORS.warning : COLORS.ink
    commands.push({ type: 'box', x, y, w: 326, h: 140, fill: '#FFFFFF', stroke: '#E5E7EB' })
    label(w.title, x + 18, y + 18, 23, color)
    label(fit(w.value, 290, 30), x + 18, y + 56, 30, color, 'bold')
    label(fit(w.target, 290, 18), x + 18, y + 104, 18, COLORS.muted)
  })
  if (snapshot.dataNotice) label(fit(snapshot.dataNotice, 670, 18), 40, 508, 18, COLORS.severe)
  label(fit('行情时间：' + (snapshot.quoteTime || '暂不可用'), 670, 19), 40, 542, 19, COLORS.muted)
  return { width: 750, height: 600, commands }
}

function renderStockShare(page, snapshot, platform = wx, cover = false) {
  return new Promise((resolve, reject) => {
    platform.createSelectorQuery().in(page).select(cover ? '#stock-share-cover-canvas' : '#stock-share-canvas').fields({ node: true }).exec(result => {
      let canvas
      try {
        canvas = result && result[0] && result[0].node
        if (!canvas) throw new Error('图片生成暂不可用，请重试')
        const ctx = canvas.getContext('2d')
        const plan = cover ? createShareCoverPlan(snapshot, ctx) : createSharePlan(snapshot, ctx)
        canvas.width = plan.width; canvas.height = plan.height
        paintSharePlan(ctx, plan)
        platform.canvasToTempFilePath({ canvas, x: 0, y: 0, width: plan.width, height: plan.height,
          destWidth: plan.width, destHeight: plan.height, fileType: 'png',
          success: result => {
            canvas.width = 1; canvas.height = 1
            resolve({ path: result.tempFilePath, width: plan.width, height: plan.height })
          }, fail: error => { canvas.width = 1; canvas.height = 1; reject(error) } }, page)
      } catch (error) { if (canvas) { canvas.width = 1; canvas.height = 1 }; reject(error) }
    })
  })
}

function openStockShareImage(path, entryPath, platform = wx) {
  return new Promise((resolve, reject) => {
    const preview = () => platform.previewImage({ urls: [path], current: path, success: resolve, fail: reject })
    if (typeof platform.showShareImageMenu !== 'function') { preview(); return }
    try {
      platform.showShareImageMenu({ path, needShowEntrance: true, entrancePath: entryPath.replace(/^\//, ''),
        success: resolve, fail: error => {
          if (/cancel/i.test(text(error && error.errMsg))) resolve({ cancelled: true })
          else preview()
        } })
    } catch (_) { preview() }
  })
}

module.exports = { captureStockShare, shareMetadata, splitLines, createSharePlan, createShareCoverPlan, paintSharePlan, renderStockShare, openStockShareImage }
