// 单股查询页：基础行情与当前状态来自后端，未来假设输入在本地即时重算。
const { searchStocks, getStockDetail } = require('../../utils/api')

function createInputRows(days, dates = []) {
  // 未来交易日假设行：股票涨幅和指数涨幅由用户输入，其余列由计算结果填充。
  return Array.from({ length: Number(days) }, (_, index) => ({
    day: `T+${index + 1}`,
    date: dates[index] || '--',
    stock: '', index: '', deviation3: '--', deviation10: '--', deviation30: '--', space: '--', deviationClass: ''
  }))
}

function buildMatrixGroups(rows) {
  const fields = [
    { key: 'stock', label: '股票涨幅', editable: true },
    { key: 'index', label: '指数涨幅', editable: true },
    { key: 'deviation3', label: '3日偏离' },
    { key: 'deviation10', label: '10日偏离' },
    { key: 'deviation30', label: '30日偏离' },
    { key: 'space', label: '安全空间' }
  ]
  const groups = []
  for (let start = 0; start < rows.length; start += 5) {
    const columns = Array.from({ length: 5 }, (_, offset) => {
      const rowIndex = start + offset
      const item = rows[rowIndex]
      return { day: item ? item.day : `T+${rowIndex + 1}`, date: item ? item.date : '--', rowIndex, placeholder: !item }
    })
    groups.push({
      groupKey: start,
      columns,
      countClass: `matrix-count-${columns.length}`,
      rows: fields.map(field => ({
        key: field.key,
        label: field.label,
        editable: Boolean(field.editable),
        values: columns.map(column => ({
          value: rows[column.rowIndex] ? rows[column.rowIndex][field.key] : '',
          placeholder: column.placeholder,
          className: rows[column.rowIndex] ? rows[column.rowIndex].deviationClass : '',
          rowIndex: column.rowIndex,
          field: field.key,
          editable: Boolean(field.editable) && !column.placeholder
        }))
      }))
    })
  }
  return groups
}

function numberValue(value) {
  const parsed = Number.parseFloat(String(value).replace('%', ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function changeClass(value) {
  const text = String(value == null ? '' : value).trim()
  if (!text || text === '--') return 'neutral'
  return text[0] === '-' ? 'down' : 'up'
}

function cumulativeReturn(values) {
  return values.reduce((total, value) => (1 + total / 100) * (1 + numberValue(value) / 100) * 100 - 100, 0)
}

function calculateSimulationRows(rows, detail) {
  const base = (detail && detail.simulationBase) || { stock: [], index: [] }
  const thresholds = (detail && detail.simulationThresholds) || { '10': { up: 100 } }
  const stockBase = Array.isArray(base.stock) ? base.stock : []
  const indexBase = Array.isArray(base.index) ? base.index : []
  return rows.map((row, rowIndex) => {
    const stockSeries = stockBase.concat(rows.slice(0, rowIndex + 1).map(item => numberValue(item.stock)))
    const indexSeries = indexBase.concat(rows.slice(0, rowIndex + 1).map(item => numberValue(item.index)))
    const metric = (window) => {
      const stockWindow = stockSeries.slice(-window)
      const indexWindow = indexSeries.slice(-window)
      if (stockWindow.length < window || indexWindow.length < window) return null
      return cumulativeReturn(stockWindow) - cumulativeReturn(indexWindow)
    }
    const deviation3 = metric(3)
    const deviation10 = metric(10)
    const deviation30 = metric(30)
    const display = value => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
    const safety = deviation10 == null ? '--' : (deviation10 >= Number(thresholds['10'].up) ? '已触发' : `${Math.max(0, Number(thresholds['10'].up) - deviation10).toFixed(2)}%`)
    const currentDeviation = deviation10 == null ? (deviation3 == null ? 0 : deviation3) : deviation10
    return {
      ...row,
      deviation3: display(deviation3),
      deviation10: display(deviation10),
      deviation30: display(deviation30),
      space: safety,
      deviationClass: currentDeviation > 0 ? 'risk' : currentDeviation < 0 ? 'safe' : ''
    }
  })
}

function presentAlerts(alerts = []) {
  return alerts.map((item) => {
    const detail = String(item.detail || '')
    const currentText = detail.split(/，阈值|,\s*阈值/)[0].replace(/^当前\s*/, '') || '数据不足'
    return {
      ...item,
      currentText,
      remainingText: item.forecast || '数据不足'
    }
  })
}

function presentWarnings(warnings = []) {
  const colors = { safe: '#128A5F', warning: '#8A6D0B', triggered: '#C0271E', unknown: '#6B7280' }
  return warnings.map((item) => {
    if (item.title === '10 日同向') {
      const up = Number(item.up || 0)
      const match = String(item.target || '').match(/(\d+(?:\.\d+)?)\s*次/)
      const limit = match ? Number(match[1]) : 0
      const count = up
      const progress = limit > 0 ? Math.min(100, count / limit * 100) : 0
      const tone = limit <= 0 ? 'unknown' : progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe'
      return { ...item, currentText: `上涨 ${up} 次`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${Math.max(0, limit - count)} 次`, ringText: tone === 'triggered' ? '已触及' : tone === 'unknown' ? '--' : `${Math.max(0, limit - count)}次`, progress: Number(progress.toFixed(2)), tone, toneColor: colors[tone] }
    }
    const rawValue = String(item.value || '').replace('%', '')
    const value = Number.parseFloat(rawValue)
    if (!Number.isFinite(value)) return { ...item, currentText: '数据不足', remainingText: '暂不可判定', ringText: '--', progress: 0, tone: 'unknown', toneColor: colors.unknown }
    const thresholds = String(item.target || '').match(/[+-]?\d+(?:\.\d+)?/g) || []
    const up = Number(thresholds[0] || 0)
    const down = Number(thresholds[1] || (up ? -up : 0))
    const target = value >= 0 ? Math.abs(up) : Math.abs(down)
    const progress = target > 0 ? Math.min(100, Math.abs(value) / target * 100) : 0
    // 黄色仅用于 3 日偏离；10/30 日偏离只有达到严重线才变红，避免把严重指标的接近值误报成警戒。
    const tone = item.title === '3 日偏离'
      ? (progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe')
      : (progress >= 100 ? 'triggered' : 'safe')
    const remaining = Math.max(0, target - Math.abs(value))
    return { ...item, currentText: `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${remaining.toFixed(2)}%`, ringText: tone === 'triggered' ? '已触及' : `${remaining.toFixed(2)}%`, progress: Number(progress.toFixed(2)), tone, toneColor: colors[tone] }
  })
}

// 单股页只接收后端给出的收益率向量和规则参数，状态、偏离及四项圆环在本地完成。
function clientCompound(values) {
  return values.reduce((total, value) => (1 + total / 100) * (1 + Number(value) / 100) * 100 - 100, 0)
}

function clientDeviation(stock, index, window) {
  const stockMap = Object.fromEntries((stock || []).filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const indexMap = Object.fromEntries((index || []).filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const dates = Object.keys(stockMap).filter(date => Object.prototype.hasOwnProperty.call(indexMap, date)).sort().slice(-window)
  if (dates.length < window) return null
  return clientCompound(dates.map(date => stockMap[date])) - clientCompound(dates.map(date => indexMap[date]))
}

function clientBestDeviation(stock, index, maxWindow) {
  const stockMap = Object.fromEntries((stock || []).filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const indexMap = Object.fromEntries((index || []).filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const dates = Object.keys(stockMap).filter(date => Object.prototype.hasOwnProperty.call(indexMap, date)).sort()
  const upper = Math.min(maxWindow, dates.length)
  const lower = maxWindow === 30 ? 27 : Math.max(3, maxWindow - 2)
  if (upper < lower) return { value: null, window: null }
  const candidates = []
  for (let window = lower; window <= upper; window += 1) {
    const range = dates.slice(-window)
    candidates.push({ value: clientCompound(range.map(date => stockMap[date])) - clientCompound(range.map(date => indexMap[date])), window })
  }
  const positive = candidates.filter(item => item.value > 0)
  return (positive.length ? positive : candidates).sort((a, b) => b.value - a.value || a.window - b.window)[0]
}

function clientTrimAfterSuspension(stock, index) {
  const stockEntries = (stock || []).filter(item => item.date).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)))
  const indexEntries = (index || []).filter(item => item.date).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)))
  if (!stockEntries.length || !indexEntries.length) return { stock: stockEntries, index: indexEntries, suspended: false }
  const stockDates = new Set(stockEntries.map(item => String(item.date)))
  const indexDates = indexEntries.map(item => String(item.date))
  const latestStock = String(stockEntries[stockEntries.length - 1].date)
  const latestIndex = String(indexEntries[indexEntries.length - 1].date)
  const gaps = indexDates.filter(day => day < latestStock && !stockDates.has(day))
  const resetDate = gaps.length ? stockEntries.find(item => String(item.date) > gaps[gaps.length - 1])?.date : ''
  if (resetDate) {
    return {
      stock: stockEntries.filter(item => String(item.date) >= String(resetDate)).slice(-30),
      index: indexEntries.filter(item => String(item.date) >= String(resetDate)).slice(-30),
      suspended: latestStock < latestIndex,
    }
  }
  return { stock: stockEntries.slice(-30), index: indexEntries.slice(-30), suspended: latestStock < latestIndex }
}

function clientMetrics(detail) {
  const input = detail && detail.calculationInput
  if (!input) return detail
  const trimmed = clientTrimAfterSuspension(Array.isArray(input.stock) ? input.stock : [], Array.isArray(input.index) ? input.index : [])
  const stock = trimmed.stock
  const index = trimmed.index
  const ordinary = Number(input.ordinaryDeviation || 20)
  const severe10 = input.severe10 || { up: 100, down: -50 }
  const severe30 = input.severe30 || { up: 200, down: -70 }
  const sameLimit = Number(input.sameDirectionThreshold || 4)
  const best10 = clientBestDeviation(stock, index, 10)
  const best30 = clientBestDeviation(stock, index, 30)
  const deviations = { 3: clientDeviation(stock, index, 3), 10: best10.value, 30: best30.value }
  const stockMap = Object.fromEntries(stock.filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const indexMap = Object.fromEntries(index.filter(item => item.date).map(item => [item.date, Number(item.return)]))
  const dates = Object.keys(stockMap).filter(date => Object.prototype.hasOwnProperty.call(indexMap, date)).sort().slice(-10)
  let up = 0; let down = 0
  for (let end = 2; end < dates.length;) {
    const range = dates.slice(end - 2, end + 1)
    const deviation = clientCompound(range.map(date => stockMap[date])) - clientCompound(range.map(date => indexMap[date]))
    if (deviation >= ordinary) { up += 1; end += 3 }
    else if (deviation <= -ordinary) { down += 1; end += 3 }
    else end += 1
  }
  const same = up
  const severeTriggered = (deviations[30] != null && (deviations[30] >= severe30.up || deviations[30] <= severe30.down)) ||
    (deviations[10] != null && (deviations[10] >= severe10.up || deviations[10] <= severe10.down)) || same >= sameLimit
  let status = '正常'; let statusClass = 'safe'
  if (severeTriggered) { status = '触及'; statusClass = 'severe' }
  else if (deviations[3] != null && Math.abs(deviations[3]) >= ordinary) { status = '警示'; statusClass = 'risk' }
  const thresholdText = (value, threshold) => value == null ? `数据不足（需 ${threshold} 个交易日）` : `阈值 +${threshold === 3 ? ordinary : threshold === 10 ? severe10.up : severe30.up}% / ${threshold === 3 ? -ordinary : threshold === 10 ? severe10.down : severe30.down}%`
  const warning = [
    { title: '3 日偏离', value: deviations[3] == null ? '--' : `${deviations[3] >= 0 ? '+' : ''}${deviations[3].toFixed(2)}%`, target: thresholdText(deviations[3], 3), className: deviations[3] == null ? 'neutral' : Math.abs(deviations[3]) >= ordinary ? 'risk' : 'safe' },
    { title: '10 日偏离', value: deviations[10] == null ? '--' : `${deviations[10] >= 0 ? '+' : ''}${deviations[10].toFixed(2)}%`, target: thresholdText(deviations[10], 10), className: deviations[10] == null ? 'neutral' : (deviations[10] >= severe10.up || deviations[10] <= severe10.down) ? 'risk' : 'safe', window: best10.window },
    { title: '30 日偏离', value: deviations[30] == null ? '--' : `${deviations[30] >= 0 ? '+' : ''}${deviations[30].toFixed(2)}%`, target: thresholdText(deviations[30], 30), className: deviations[30] == null ? 'neutral' : (deviations[30] >= severe30.up || deviations[30] <= severe30.down) ? 'risk' : 'safe', window: best30.window },
    { title: '10 日同向', value: '', up: String(up), down: String(down), target: `阈值 ${sameLimit} 次`, className: same >= sameLimit ? 'risk' : 'safe' }
  ]
  return { ...detail, status, statusClass, statusDisplay: status === '正常' ? '状态安全' : '状态异常', statusIcon: statusClass === 'safe' ? '✓' : '!', warnings: presentWarnings(warning) }
}

Page({
  data: {
    query: '', suggestions: [], suggestionHint: false, selected: null, detail: null,
    detailLoading: false, detailError: '', searchError: '', searchFocused: false,
    futureTradeDates: [],
    dayOptions: ['10'], simulationExpanded: false, visibleDays: 2,
    inputRows: createInputRows(2), matrixGroups: buildMatrixGroups(createInputRows(2))
  },
  onLoad(options) { if (options.ts_code) this.loadStockByCode(options.ts_code) },
  onShow() {
    this.syncTabBar()
    const pending = getApp().globalData.pendingStock
    if (pending) { getApp().globalData.pendingStock = null; this.chooseStock(pending) }
  },
  syncTabBar() {
    const tabBar = this.getTabBar && this.getTabBar()
    if (tabBar) tabBar.setData({ selected: 0 })
  },
  onQueryInput(event) {
    const query = event.detail.value
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.searchRequestId = (this.searchRequestId || 0) + 1
    const requestId = this.searchRequestId
    this.setData({ query, suggestions: [], suggestionHint: false, searchError: '', selected: null, detail: null, detailError: '' })
    if (!query.trim()) return
    this.searchTimer = setTimeout(() => {
      searchStocks(query).then((result) => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: result.items || [], suggestionHint: Boolean(result.hasMore), searchError: '' })
      }).catch(() => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: [], suggestionHint: false, searchError: '搜索失败，请稍后重试' })
      })
    }, 250)
  },
  onSearchFocus() { this.setData({ searchFocused: true }) },
  onSearchBlur() { this.setData({ searchFocused: false }) },
  clearQuery() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.searchRequestId = (this.searchRequestId || 0) + 1
    this.setData({ query: '', suggestions: [], suggestionHint: false, searchError: '', selected: null, detail: null, detailError: '' })
  },
  loadStockByCode(tsCode) {
    if (!tsCode) return
    this.chooseStock({ ts_code: tsCode, symbol: tsCode.split('.')[0], name: '正在加载' })
  },
  chooseStock(eventOrStock) {
    const stock = eventOrStock && eventOrStock.currentTarget
      ? this.data.suggestions[eventOrStock.currentTarget.dataset.index]
      : eventOrStock
    if (!stock || !stock.ts_code) return
    getApp().globalData.lastStock = stock
    const inputRows = createInputRows(2, this.data.futureTradeDates)
    this.detailRequestId = (this.detailRequestId || 0) + 1
    const requestId = this.detailRequestId
    this.setData({ selected: stock, detail: null, detailLoading: true, detailError: '', query: stock.name === '正在加载' ? stock.ts_code : `${stock.name} ${stock.symbol}`, suggestions: [], suggestionHint: false, inputRows, matrixGroups: buildMatrixGroups(inputRows), simulationExpanded: false, visibleDays: 2 })
    getStockDetail(stock.ts_code).then((detail) => {
      if (requestId !== this.detailRequestId) return
      const futureTradeDates = detail.futureTradeDates || []
      const rows = calculateSimulationRows(createInputRows(2, futureTradeDates), detail)
      const displayDetail = clientMetrics({
        ...detail,
        statusDisplay: detail.status === '正常' ? '状态安全' : '状态异常',
        change: detail.change == null || detail.change === '' ? '--' : String(detail.change),
        changeClass: changeClass(detail.change),
        dataQuality: detail.dataQuality || {},
        warnings: presentWarnings(detail.warnings),
        alerts: presentAlerts(detail.alerts)
      })
      this.setData({ selected: { ...stock, ...detail }, detail: displayDetail, detailLoading: false, detailError: '', futureTradeDates, inputRows: rows, matrixGroups: buildMatrixGroups(rows), query: `${detail.name} ${detail.symbol}` })
    }).catch(() => {
      if (requestId !== this.detailRequestId) return
      this.setData({ detailLoading: false, detailError: '行情暂时不可用，请稍后重试' })
    })
  },
  expandSimulation() {
    if (this.data.simulationExpanded) return
    const nextRows = createInputRows(10, this.data.futureTradeDates)
    const inputRows = calculateSimulationRows(nextRows.map((item, index) => ({ ...item, stock: this.data.inputRows[index] ? this.data.inputRows[index].stock : '', index: this.data.inputRows[index] ? this.data.inputRows[index].index : '' })), this.data.detail)
    this.setData({ simulationExpanded: true, visibleDays: 10, inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  editRow(event) {
    const { row, field } = event.currentTarget.dataset
    const value = event.detail.value
    const rows = this.data.inputRows.map((item, index) => index === Number(row) ? { ...item, [field]: value } : item)
    const calculatedRows = calculateSimulationRows(rows, this.data.detail)
    this.setData({ inputRows: calculatedRows, matrixGroups: buildMatrixGroups(calculatedRows) })
  },
  clearInputs() {
    const inputRows = calculateSimulationRows(createInputRows(this.data.visibleDays, this.data.futureTradeDates), this.data.detail)
    this.setData({ inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  onUnload() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
  },
})
