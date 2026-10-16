// 单股查询页：基础行情与当前状态来自后端，未来假设输入在本地即时重算。
const { searchStocks, getStockDetail } = require('../../utils/api')

function createInputRows(days, dates = []) {
  // 未来交易日默认按股票和指数均为 0% 计算，用户修改时即时重算。
  return Array.from({ length: Number(days) }, (_, index) => ({
    day: `T+${index + 1}`,
    date: dates[index] || '--',
    stock: '0', index: '0', stockSelectAll: false, indexSelectAll: false,
    deviation3: '--', deviation10: '--', deviation30: '--', space: '--', deviationClass: ''
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
          className: rows[column.rowIndex]
            ? (rows[column.rowIndex][`${field.key}Class`] || rows[column.rowIndex].deviationClass || '')
            : '',
          selectionStart: rows[column.rowIndex] && rows[column.rowIndex][`${field.key}SelectAll`] ? 0 : -1,
          selectionEnd: rows[column.rowIndex] && rows[column.rowIndex][`${field.key}SelectAll`]
            ? String(rows[column.rowIndex][field.key] == null ? '' : rows[column.rowIndex][field.key]).length
            : -1,
          rowIndex: column.rowIndex,
          field: field.key,
          editable: Boolean(field.editable) && !column.placeholder
        }))
      }))
    })
  }
  return groups
}

function parsePercent(value) {
  if (value == null || String(value).trim() === '') return null
  const parsed = Number.parseFloat(String(value).replace('%', ''))
  return Number.isFinite(parsed) ? parsed : null
}

function changeClass(value) {
  const text = String(value == null ? '' : value).trim()
  if (!text || text === '--') return 'neutral'
  return text[0] === '-' ? 'down' : 'up'
}

function formatUpdatedAt(value) {
  const text = String(value == null ? '' : value).trim()
  if (!text) return ''
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6)} 15:00`
  return text.replace('T', ' ').replace(/\.\d+(Z)?$/, '').slice(0, 16)
}

function cumulativeReturn(values) {
  return values.reduce((total, value) => (1 + total / 100) * (1 + value / 100) * 100 - 100, 0)
}

function bestDeviation(stockValues, indexValues, maxWindow) {
  // 与后端 _best_vector_deviation 保持一致：枚举规则窗口内当前端点的有效收益区间，取最大上涨偏离。
  const upperLimit = maxWindow === 10 || maxWindow === 30 ? maxWindow - 1 : maxWindow
  const upper = Math.min(upperLimit, stockValues.length, indexValues.length)
  const lower = 1
  if (upper < lower) return { value: null, window: null }
  const results = []
  for (let window = lower; window <= upper; window += 1) {
    results.push({
      value: cumulativeReturn(stockValues.slice(-window)) - cumulativeReturn(indexValues.slice(-window)),
      window
    })
  }
  const positive = results.filter(item => item.value > 0)
  const candidates = positive.length ? positive : results
  candidates.sort((left, right) => positive.length
    ? right.value - left.value || left.window - right.window
    : left.value - right.value || left.window - right.window)
  return candidates[0]
}

function calculateSimulationRows(rows, detail) {
  const base = (detail && detail.simulationBase) || { stock: [], index: [] }
  const thresholds = (detail && detail.simulationThresholds) || { '10': { up: 100 } }
  const stockBase = Array.isArray(base.stock) ? base.stock : []
  const indexBase = Array.isArray(base.index) ? base.index : []
  let complete = true
  return rows.map((row, rowIndex) => {
    const stockValue = parsePercent(row.stock)
    const indexValue = parsePercent(row.index)
    if (stockValue == null || indexValue == null) complete = false
    if (!complete) {
      return { ...row, deviation3: '--', deviation10: '--', deviation30: '--', space: '--', deviation3Window: null, deviation10Window: null, deviation30Window: null, deviationClass: '' }
    }
    const stockSeries = stockBase.concat(rows.slice(0, rowIndex + 1).map(item => parsePercent(item.stock)))
    const indexSeries = indexBase.concat(rows.slice(0, rowIndex + 1).map(item => parsePercent(item.index)))
    const metric3 = bestDeviation(stockSeries, indexSeries, 3)
    const metric10 = bestDeviation(stockSeries, indexSeries, 10)
    const metric30 = bestDeviation(stockSeries, indexSeries, 30)
    const deviation3 = metric3.value
    const deviation10 = metric10.value
    const deviation30 = metric30.value
    const display = value => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
    const ordinaryThreshold = detail && detail.simulationThresholds && detail.simulationThresholds['3']
      ? Number(detail.simulationThresholds['3'].up)
      : detail && detail.calculationInput ? Number(detail.calculationInput.ordinaryDeviation) : NaN
    const safetyMetrics = [
      { deviation: deviation3, threshold: ordinaryThreshold },
      { deviation: deviation10, threshold: thresholds['10'] && Number(thresholds['10'].up) },
      { deviation: deviation30, threshold: thresholds['30'] && Number(thresholds['30'].up) }
    ].filter(item => item.deviation != null && Number.isFinite(item.threshold))
    const safety = safetyMetrics.length === 0
      ? '--'
      : safetyMetrics.some(item => item.deviation >= item.threshold)
        ? '已触发'
        : `${Math.min(...safetyMetrics.map(item => Math.max(0, item.threshold - item.deviation))).toFixed(2)}%`
    const currentDeviation = deviation10 == null ? (deviation3 == null ? 0 : deviation3) : deviation10
    const deviationClass = value => value == null ? '' : value > 0 ? 'risk' : value < 0 ? 'safe' : ''
    return {
      ...row,
      deviation3: display(deviation3),
      deviation10: display(deviation10),
      deviation30: display(deviation30),
      space: safety,
      deviation3Window: metric3.window,
      deviation10Window: metric10.window,
      deviation30Window: metric30.window,
      deviation3Class: deviationClass(deviation3),
      deviation10Class: deviationClass(deviation10),
      deviation30Class: deviationClass(deviation30),
      deviationClass: deviationClass(currentDeviation)
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
  const colors = { safe: '#4B5563', warning: '#6B7280', triggered: '#1C1C1E', unknown: '#9CA3AF' }
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
    const up = Math.abs(Number(thresholds[0] || 0))
    // 负向偏离仅作为原始值展示，不参与上涨规则的进度、触发或剩余距离。
    const progress = up > 0 ? Math.min(100, Math.max(0, value) / up * 100) : 0
    // 黄色仅用于 3 日偏离；10/30 日偏离只有达到严重线才变红，避免把严重指标的接近值误报成警戒。
    const tone = item.title === '3 日偏离'
      ? (progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe')
      : (progress >= 100 ? 'triggered' : 'safe')
    const remaining = Math.max(0, up - Math.max(0, value))
    const actualWindow = Number(item.actualWindow)
    const windowText = Number.isFinite(actualWindow) && actualWindow > 0 ? `${actualWindow} 日 ` : ''
    return { ...item, currentText: `${windowText}${value >= 0 ? '+' : ''}${value.toFixed(2)}%`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${remaining.toFixed(2)}%`, ringText: tone === 'triggered' ? '已触及' : `${remaining.toFixed(2)}%`, progress: Number(progress.toFixed(2)), tone, toneColor: colors[tone] }
  })
}

Page({
  data: {
    query: '', suggestions: [], suggestionHint: false, suggestionTotal: 0, selected: null, detail: null,
    detailLoading: false, detailError: '', searchError: '', searchFocused: false, dataUpdatedAt: '',
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
    this.setData({ query, suggestions: [], suggestionHint: false, suggestionTotal: 0, searchError: '', selected: null, detail: null, detailError: '', dataUpdatedAt: '' })
    if (!query.trim()) return
    this.searchTimer = setTimeout(() => {
      searchStocks(query).then((result) => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: result.items || [], suggestionHint: Boolean(result.hasMore), suggestionTotal: Number(result.total || 0), searchError: '' })
      }).catch(() => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: [], suggestionHint: false, suggestionTotal: 0, searchError: '搜索失败，请稍后重试' })
      })
    }, 250)
  },
  onSearchFocus() { this.setData({ searchFocused: true }) },
  onSearchBlur() { this.setData({ searchFocused: false }) },
  clearQuery() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.searchRequestId = (this.searchRequestId || 0) + 1
    this.setData({ query: '', suggestions: [], suggestionHint: false, suggestionTotal: 0, searchError: '', selected: null, detail: null, detailError: '', dataUpdatedAt: '' })
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
    this.setData({ selected: stock, detail: null, detailLoading: true, detailError: '', dataUpdatedAt: '', query: stock.name === '正在加载' ? stock.ts_code : `${stock.name} ${stock.symbol}`, suggestions: [], suggestionHint: false, inputRows, matrixGroups: buildMatrixGroups(inputRows), simulationExpanded: false, visibleDays: 2 })
    getStockDetail(stock.ts_code).then((detail) => {
      if (requestId !== this.detailRequestId) return
      const futureTradeDates = detail.futureTradeDates || []
      const rows = calculateSimulationRows(createInputRows(2, futureTradeDates), detail)
      const displayDetail = {
        ...detail,
        statusDisplay: detail.statusDisplay || (detail.status === '正常' ? '状态安全' : '状态异常'),
        marketStatusDisplay: detail.marketStatusDisplay || (detail.statusReason ? `已触发 · ${detail.statusReason}` : '当前行情规则未触发'),
        change: detail.change == null || detail.change === '' ? '--' : String(detail.change),
        changeClass: changeClass(detail.change),
        dataQuality: detail.dataQuality || {},
        warnings: presentWarnings(detail.warnings),
        alerts: presentAlerts(detail.alerts)
      }
      const dataUpdatedAt = formatUpdatedAt((detail.dataQuality || {}).quoteUpdatedAt || detail.tradeDate)
      this.setData({ selected: { ...stock, ...detail }, detail: displayDetail, detailLoading: false, detailError: '', dataUpdatedAt, futureTradeDates, inputRows: rows, matrixGroups: buildMatrixGroups(rows), query: `${detail.name} ${detail.symbol}` })
    }).catch((error) => {
      if (requestId !== this.detailRequestId) return
      const message = error && error.message ? String(error.message) : ''
      const isDataError = /数据库|收益向量|基线|交易区间|共同交易/.test(message)
      this.setData({ detailLoading: false, detailError: isDataError ? message : '行情暂时不可用，请稍后重试' })
    })
  },
  expandSimulation() {
    if (this.data.simulationExpanded) return
    const nextRows = createInputRows(10, this.data.futureTradeDates)
    const inputRows = calculateSimulationRows(nextRows.map((item, index) => ({ ...item, stock: this.data.inputRows[index] ? this.data.inputRows[index].stock : item.stock, index: this.data.inputRows[index] ? this.data.inputRows[index].index : item.index })), this.data.detail)
    this.setData({ simulationExpanded: true, visibleDays: 10, inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  editRow(event) {
    const { row, field } = event.currentTarget.dataset
    const value = event.detail.value
    const rows = this.data.inputRows.map((item, index) => index === Number(row)
      ? { ...item, [field]: value, stockSelectAll: false, indexSelectAll: false }
      : item)
    const calculatedRows = calculateSimulationRows(rows, this.data.detail)
    this.setData({ inputRows: calculatedRows, matrixGroups: buildMatrixGroups(calculatedRows) })
  },
  selectInput(event) {
    const { row, field } = event.currentTarget.dataset
    const rows = this.data.inputRows.map((item, index) => index === Number(row)
      ? { ...item, stockSelectAll: field === 'stock', indexSelectAll: field === 'index' }
      : item)
    this.setData({ inputRows: rows, matrixGroups: buildMatrixGroups(rows) })
  },
  blurInput(event) {
    const { row } = event.currentTarget.dataset
    const rows = this.data.inputRows.map((item, index) => index === Number(row)
      ? { ...item, stockSelectAll: false, indexSelectAll: false }
      : item)
    this.setData({ inputRows: rows, matrixGroups: buildMatrixGroups(rows) })
  },
  clearInputs() {
    const inputRows = calculateSimulationRows(createInputRows(this.data.visibleDays, this.data.futureTradeDates), this.data.detail)
    this.setData({ inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  onUnload() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
  },
})

// 纯计算函数导出给前端回归测试；小程序运行时不影响 Page 注册。
if (typeof module !== 'undefined') module.exports = { bestDeviation, calculateSimulationRows, createInputRows }
