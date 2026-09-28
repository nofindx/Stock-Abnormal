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
      const down = Number(item.down || 0)
      const match = String(item.target || '').match(/(\d+(?:\.\d+)?)\s*次/)
      const limit = match ? Number(match[1]) : 0
      const count = Math.max(up, down)
      const progress = limit > 0 ? Math.min(100, count / limit * 100) : 0
      const tone = limit <= 0 ? 'unknown' : progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe'
      return { ...item, currentText: `上涨 ${up} 次 / 下跌 ${down} 次`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${Math.max(0, limit - count)} 次`, progress: Number(progress.toFixed(2)), tone, toneColor: colors[tone] }
    }
    const rawValue = String(item.value || '').replace('%', '')
    const value = Number.parseFloat(rawValue)
    if (!Number.isFinite(value)) return { ...item, currentText: '数据不足', remainingText: '暂不可判定', progress: 0, tone: 'unknown', toneColor: colors.unknown }
    const thresholds = String(item.target || '').match(/[+-]?\d+(?:\.\d+)?/g) || []
    const up = Number(thresholds[0] || 0)
    const down = Number(thresholds[1] || (up ? -up : 0))
    const target = value >= 0 ? Math.abs(up) : Math.abs(down)
    const progress = target > 0 ? Math.min(100, Math.abs(value) / target * 100) : 0
    const tone = progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe'
    const remaining = Math.max(0, target - Math.abs(value))
    return { ...item, currentText: `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${remaining.toFixed(2)}%`, progress: Number(progress.toFixed(2)), tone, toneColor: colors[tone] }
  })
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
  openRules() {
    if (this.rulesNavigating) return
    this.rulesNavigating = true
    wx.navigateTo({
      url: '/pages/rules/index',
      animationType: 'none',
      animationDuration: 0,
      complete: () => { this.rulesNavigating = false }
    })
  },
  onShow() {
    const pending = getApp().globalData.pendingStock
    if (pending) { getApp().globalData.pendingStock = null; this.chooseStock(pending) }
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
      const displayDetail = {
        ...detail,
        change: detail.change == null || detail.change === '' ? '--' : String(detail.change),
        changeClass: changeClass(detail.change),
        dataQuality: detail.dataQuality || {},
        warnings: presentWarnings(detail.warnings),
        alerts: presentAlerts(detail.alerts)
      }
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
  openPrivacy() { wx.navigateTo({ url: '/pages/privacy/index', animationType: 'none' }) },
  openAgreement() { wx.navigateTo({ url: '/pages/agreement/index', animationType: 'none' }) }
})
