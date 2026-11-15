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
      number: text(w.currentNumber || w.currentText || '--'), unit: text(w.currentUnit), numberSize: Number(w.currentFontSize) || 30,
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
  // Omit the custom title. WeChat owns the native title area and may show the app name.
  return {
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
      ctx.fillStyle = c.fill; ctx.strokeStyle = c.stroke; ctx.lineWidth = 1
      if (c.radius) {
        const r = Math.min(c.radius, c.w / 2, c.h / 2), x = c.x, y = c.y, w = c.w, h = c.h
        ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y)
        ctx.quadraticCurveTo(x + w, y, x + w, y + r); ctx.lineTo(x + w, y + h - r)
        ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h); ctx.lineTo(x + r, y + h)
        ctx.quadraticCurveTo(x, y + h, x, y + h - r); ctx.lineTo(x, y + r)
        ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath(); ctx.fill(); ctx.stroke()
      } else {
        ctx.fillRect(c.x, c.y, c.w, c.h); ctx.strokeRect(c.x, c.y, c.w, c.h)
      }
    } else if (c.type === 'circle') {
      ctx.fillStyle = c.fill; ctx.beginPath(); ctx.arc(c.x, c.y, c.radius, 0, Math.PI * 2); ctx.fill()
    } else if (c.type === 'text') {
      ctx.font = `${c.weight} ${c.size}px sans-serif`; ctx.fillStyle = c.color; ctx.textAlign = c.align
      ctx.fillText(c.value, c.x, c.y)
    } else if (c.type === 'ring') {
      ctx.lineWidth = c.lineWidth || 12; ctx.strokeStyle = '#E5E7EB'
      ctx.beginPath(); ctx.arc(c.x, c.y, c.radius || 49, 0, Math.PI * 2); ctx.stroke()
      if (c.progress > 0) {
        ctx.strokeStyle = c.color; ctx.beginPath()
        const full = c.progress >= 100
        ctx.arc(c.x, c.y, c.radius || 49, full ? 0 : -Math.PI / 2, full ? Math.PI * 2 : -Math.PI / 2 + Math.PI * 2 * c.progress / 100); ctx.stroke()
      }
    }
  })
}

function createShareCoverPlan(snapshot, ctx) {
  const commands = [{ type: 'box', x: 0, y: 0, w: 750, h: 600, fill: '#F2F3F5', stroke: '#F2F3F5' }]
  const box = (x, y, w, h, fill, stroke, radius) => commands.push({ type: 'box', x, y, w, h, fill, stroke, radius })
  const label = (value, x, y, size, color = COLORS.ink, weight = 'normal', align = 'left') =>
    commands.push({ type: 'text', value: text(value), x, y, size, color, weight, align })
  const measure = (value, size, weight = 'normal') => {
    ctx.font = `${weight} ${size}px sans-serif`
    return ctx.measureText(text(value)).width
  }
  const fit = (value, width, size, weight = 'normal') => {
    const chars = Array.from(text(value)), originalLength = chars.length
    while (chars.length && measure(chars.join('') + (chars.length < originalLength ? '…' : ''), size, weight) > width) chars.pop()
    return chars.join('') + (chars.length < originalLength ? '…' : '')
  }
  // Mirror the stock page's final CSS: gray page, tinted status card, colored pills,
  // black metric values, and a white ring center even on a tinted warning card.
  const palettes = {
    base: { fill: '#FFFFFF', border: '#E5E5EA', pill: '#EAF3FC', pillBorder: '#B9D6F2', ink: '#185A96' },
    warning: { fill: '#FFFAE8', border: '#F0D36A', pill: '#FFF5CC', pillBorder: '#F0D36A', ink: '#8A6D0B' },
    severe: { fill: '#FFF5F5', border: '#E7A8A2', pill: '#FDF3F2', pillBorder: '#E7A8A2', ink: '#C0271E' },
    unknown: { fill: '#FFFFFF', border: '#E5E5EA', pill: '#F3F8FD', pillBorder: '#DCEBFA', ink: '#6B7280' }
  }
  const statusClass = snapshot.statusClass
  const triggered = statusClass === 'current-triggered'
  const severeCard = ['risk', 'severe', 'monitoring'].includes(statusClass)
  const statusCard = statusClass === 'warning' ? palettes.warning : severeCard ? palettes.severe : palettes.base
  box(28, 20, 694, 132, triggered ? '#FFF5F5' : severeCard ? '#FDF3F2' : statusCard.fill,
    triggered ? '#D93025' : severeCard ? '#E7A8A2' : statusCard.border, 14)
  const changeColor = snapshot.change.startsWith('-') ? '#16835D' : snapshot.change.startsWith('+') ? '#D93025' : '#6B7280'
  const priceWidth = measure(snapshot.price, 40, 'bold'), changeWidth = measure(snapshot.change, 28, 'bold')
  const quoteX = 704 - priceWidth - 12 - changeWidth
  const name = fit(snapshot.name, Math.max(0, quoteX - 46 - measure(snapshot.symbol, 20) - 32), 30, 'bold')
  label(name, 46, 42, 30, '#1A1A1A', 'bold')
  label(snapshot.symbol, 46 + measure(name, 30, 'bold') + 12, 52, 20, '#6B7280')
  label(snapshot.price, quoteX, 32, 40, changeColor, 'bold')
  label(snapshot.change, 704 - changeWidth, 44, 28, changeColor, 'bold')
  box(46, 88, 658, 1, '#F2F2F7', '#F2F2F7', 0)
  const tone = snapshot.summaryTone
  const statusPill = ['risk', 'severe'].includes(tone) ? palettes.severe :
    ['warning', 'unknown'].includes(tone) ? palettes.warning : palettes.base
  const statusText = fit(snapshot.status || '状态暂不可用', 634, 19, 'bold')
  const pillWidth = measure(statusText, 19, 'bold') + 24
  box(46, 104, pillWidth, 34, statusPill.pill, statusPill.pillBorder, 7)
  label(statusText, 58, 111, 19, statusPill.ink, 'bold')
  snapshot.warnings.slice(0, 4).forEach((w, i) => {
    const x = 28 + i % 2 * 357, y = 172 + Math.floor(i / 2) * 196
    const palette = palettes[w.tone] || palettes.base
    box(x, y, 337, 176, palette.fill, palette.border, 12)
    box(x + 16, y + 14, 112, 38, palette.pill, palette.pillBorder, 7)
    label(fit(w.title, 96, 19, 'bold'), x + 72, y + 24, 19, palette.ink, 'bold', 'center')
    const number = w.number || w.value, unit = w.unit || ''
    let numberSize = Math.min(30, Math.max(20, w.numberSize || 30))
    const unitWidth = unit ? measure(unit, 20, 'bold') + 1 : 0
    while (numberSize > 18 && measure(number, numberSize, 'bold') > 155 - unitWidth) numberSize--
    const displayNumber = fit(number, 155 - unitWidth, numberSize, 'bold')
    label(displayNumber, x + 16, y + 112 - numberSize, numberSize, COLORS.ink, 'bold')
    if (unit) label(unit, x + 17 + measure(displayNumber, numberSize, 'bold'), y + 92, 20, COLORS.ink, 'bold')
    label(fit(w.target, 155, 20), x + 16, y + 139, 20, '#4B5563')
    const ringX = x + 249, ringY = y + 88
    commands.push({ type: 'ring', x: ringX, y: ringY, radius: 61, lineWidth: 18,
      progress: w.progress || 0, color: w.color || '#B9D6F2' })
    commands.push({ type: 'circle', x: ringX, y: ringY, radius: 52, fill: '#FFFFFF' })
    let ringSize = 20
    const ringText = w.ring || '—'
    while (ringSize > 16 && measure(ringText, ringSize, 'bold') > 92) ringSize--
    label(fit(ringText, 92, ringSize, 'bold'), ringX, ringY - ringSize / 2, ringSize, '#4B5563', 'bold', 'center')
  })
  const footer = snapshot.dataNotice || ('行情时间：' + (snapshot.quoteTime || '暂不可用'))
  label(fit(footer, 694, 18), 28, 565, 18, snapshot.dataNotice ? COLORS.severe : '#6B7280')
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

function saveStockShareImage(path, platform = wx) {
  return new Promise((resolve, reject) => {
    if (typeof platform.saveImageToPhotosAlbum !== 'function') {
      reject(new Error('当前微信版本不支持保存图片')); return
    }
    // The native API triggers WeChat privacy/album authorization only after an explicit tap.
    // No application share sheet, album reads, or global authorization request.
    platform.saveImageToPhotosAlbum({ filePath: path, success: resolve, fail: error => {
      const message = text(error && error.errMsg)
      const errno = Number(error && error.errno)
      if (/scope is not declared|api banned/i.test(message) || errno === 112) {
        reject(new Error('保存功能暂不可用，请稍后重试'))
      } else if (/auth|deny|denied|privacy/i.test(message) || [103, 104].includes(errno)) {
        reject(new Error('未获保存授权，图片未保存'))
      } else if (/cancel/i.test(message)) {
        reject(new Error('已取消，图片未保存'))
      } else reject(new Error('图片保存失败，请稍后重试'))
    } })
  })
}

module.exports = { captureStockShare, shareMetadata, splitLines, createSharePlan, createShareCoverPlan, paintSharePlan, renderStockShare, saveStockShareImage }
