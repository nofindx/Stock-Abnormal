// 单股查询页：基础行情与当前状态来自后端，未来假设输入在本地即时重算。
const { searchStocks, getStockDetail, getMonitor } = require('../../utils/api')

function formatSimulationDate(value) {
  const text = String(value == null ? '' : value).trim()
  const dateMatch = text.match(/^\d{4}([\/-])(\d{1,2})[\/-](\d{1,2})/)
  if (dateMatch) return `${String(dateMatch[2]).padStart(2, '0')}${dateMatch[1]}${String(dateMatch[3]).padStart(2, '0')}`
  const digits = text.replace(/[^\d]/g, '')
  if (digits.length >= 8) return `${digits.slice(4, 6)}-${digits.slice(6, 8)}`
  return text
}

function createInputRows(days, dates = []) {
  // 输入框保持为空；计算时空值按 0% 处理，用户修改时即时重算。
  return Array.from({ length: Number(days) }, (_, index) => ({
    day: `T+${index + 1}`,
    date: formatSimulationDate(dates[index] || '--'),
    stock: '', index: '', stockSelectAll: false, indexSelectAll: false,
    deviation10: '--', deviation30: '--', space: '--', deviationClass: ''
  }))
}

function buildMatrixGroups(rows) {
  const fields = [
    { key: 'stock', label: '股票涨幅', editable: true },
    { key: 'index', label: '指数涨幅', editable: true },
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

function simulationInputValue(value) {
  const text = String(value == null ? '' : value).trim()
  if (!text) return 0
  return parsePercent(value)
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
  return text.replace('T', ' ').replace(/\.\d+(Z)?$/, '').slice(0, 19)
}

function formatMonthDay(value) {
  const text = String(value == null ? '' : value).trim()
  const digits = text.replace(/[^\d]/g, '')
  if (digits.length >= 8) return `${digits.slice(4, 6)}/${digits.slice(6, 8)}`
  const match = text.match(/^(\d{1,2})[/-](\d{1,2})$/)
  if (match) return `${String(match[1]).padStart(2, '0')}/${String(match[2]).padStart(2, '0')}`
  return '--/--'
}

function monitoringRuleLabel(monitoring = {}) {
  const explicit = String(monitoring.ruleText || monitoring.monitorReason || '').trim()
  if (explicit) return explicit
  const tone = String(monitoring.riskTone || '').trim()
  const code = String(monitoring.ts_code || monitoring.symbol || '').match(/\d{6}/)
  const symbol = code ? code[0] : ''
  const isBse = /^(4|8|9)/.test(symbol)
  const direction = String(monitoring.eventDirection || '').toLowerCase() === 'down' ? '跌幅' : '涨幅'
  if (tone === 'severe-10d') {
    const threshold = direction === '涨幅' ? (isBse ? 150 : 100) : (isBse ? 60 : 50)
    return `连续10个交易日内收盘价${direction}偏离值累计达到 ${direction === '涨幅' ? '+' : '-'}${threshold}%`
  }
  if (tone === 'severe-30d') {
    const threshold = direction === '涨幅' ? (isBse ? 300 : 200) : (isBse ? 75 : 70)
    return `连续30个交易日内收盘价${direction}偏离值累计达到 ${direction === '涨幅' ? '+' : '-'}${threshold}%`
  }
  if (tone === 'delisting-warning') return '股票进入退市整理期'
  if (tone === 'ordinary') return '交易所将对该证券的异常交易行为进行进一步从严认定'
  const type = String(monitoring.monitorType || '').trim()
  return type || '重点监管'
}

function dateKey(value) {
  return String(value == null ? '' : value).replace(/[^\d]/g, '').slice(0, 8)
}

function localTodayKey() {
  const now = new Date()
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
}

function remainingMonitoringDays(monitoring = {}, today = localTodayKey()) {
  const dates = (monitoring.monitorDates || []).map(dateKey).filter(Boolean).sort()
  const end = dateKey(monitoring.monitorEndDate || monitoring.monitorEnd || dates[dates.length - 1])
  if (end && today) {
    const endDate = Date.UTC(Number(end.slice(0, 4)), Number(end.slice(4, 6)) - 1, Number(end.slice(6, 8)))
    const todayKey = dateKey(today)
    const todayDate = Date.UTC(Number(todayKey.slice(0, 4)), Number(todayKey.slice(4, 6)) - 1, Number(todayKey.slice(6, 8)))
    if (Number.isFinite(endDate) && Number.isFinite(todayDate)) return String(Math.max(0, Math.round((endDate - todayDate) / 86400000)))
  }
  const fallback = monitoring.remainingDays != null ? monitoring.remainingDays : monitoring.days
  return fallback == null || fallback === '' ? '--' : String(fallback)
}

function formatMonitoringSummary(monitoring, today = localTodayKey()) {
  if (!monitoring || monitoring.active === false) return null
  const start = monitoring.monitorStartDate || monitoring.monitorStart
  const end = monitoring.monitorEndDate || monitoring.monitorEnd
  return {
    dateText: `${formatMonthDay(start)}-${formatMonthDay(end)}`,
    ruleText: monitoringRuleLabel(monitoring),
    remainingText: remainingMonitoringDays(monitoring, today)
  }
}

function monitoringPriority(item = {}) {
  return { 'severe-30d': 4, 'severe-10d': 3, 'delisting-warning': 2, ordinary: 1 }[String(item.riskTone || '')] || 0
}

function findStockMonitoring(items = [], tsCode = '') {
  const code = String(tsCode || '').split('.')[0]
  return (items || [])
    .filter(item => String(item.ts_code || item.symbol || '').split('.')[0] === code)
    .sort((left, right) => monitoringPriority(right) - monitoringPriority(left) || String(right.monitorEndDate || '').localeCompare(String(left.monitorEndDate || '')))[0] || null
}

const STOCK_HISTORY_KEY = 'stockSearchHistory'

function normalizeTradeDate(value) {
  return String(value || '').replace(/[^\d]/g, '').slice(0, 8)
}

function buildNextDayForecast(forecast = {}) {
  if (typeof forecast.available !== 'boolean') return { line: '', text: '预测数据版本未同步' }
  const tradeDate = normalizeTradeDate(forecast.baseTradeDate)
  const nextTradeDate = normalizeTradeDate(forecast.targetTradeDate)
  if (!tradeDate || !nextTradeDate || !forecast.available) return { line: '', text: '次日预测暂不可用' }
  const line = `按 ${tradeDate.slice(0, 4)}-${tradeDate.slice(4, 6)}-${tradeDate.slice(6)} 收盘数据预测 ${nextTradeDate.slice(0, 4)}-${nextTradeDate.slice(4, 6)}-${nextTradeDate.slice(6)}`
  const candidates = forecast.rules || []
  if (candidates.some(item => !String(item.rule || '').trim())) return { line, text: '次日预测规则说明暂不可用' }
  const rules = [...new Set(candidates.map(item => String(item.rule).trim()))]
  return {
    line,
    text: rules.length ? `次日可能触发：${rules.join('；')}` : '未发现下一交易日可触发的严重异动条件'
  }
}

function summaryStatus(detail) {
  const marketStatus = detail.marketStatus || detail.status || '正常'
  const forecastRules = detail.nextDayForecast && Array.isArray(detail.nextDayForecast.rules)
    ? detail.nextDayForecast.rules
    : []
  if (marketStatus !== '正常' || forecastRules.length > 0) return { text: '存在异动风险', tone: 'risk' }
  return { text: '暂无异动风险', tone: 'safe' }
}

function cumulativeReturn(values) {
  return values.reduce((total, value) => (1 + total / 100) * (1 + value / 100) * 100 - 100, 0)
}

function bestDeviation(stockValues, indexValues, maxWindow) {
  // 与后端 _best_vector_deviation 保持一致：枚举回看窗口内所有连续收益区间。
  const upperLimit = maxWindow
  const count = Math.min(maxWindow, stockValues.length, indexValues.length)
  const stocks = stockValues.slice(-count)
  const indexes = indexValues.slice(-count)
  const upper = Math.min(upperLimit, stocks.length, indexes.length)
  const lower = 1
  if (upper < lower) return { value: null, window: null }
  const results = []
  for (let window = lower; window <= upper; window += 1) {
    results.push({
      value: cumulativeReturn(stocks.slice(-window)) - cumulativeReturn(indexes.slice(-window)),
      window
    })
  }
  const positive = results.filter(item => item.value > 0)
  const candidates = positive.length ? positive : results
  candidates.sort((left, right) => positive.length
    ? right.value - left.value || left.window - right.window
    : left.value - right.value || left.window - right.window)
  return { ...candidates[0], downValue: Math.min(...results.map(item => item.value)) }
}

function calculateSimulationRows(rows, detail) {
  const base = (detail && detail.simulationBase) || { stock: [], index: [] }
  const thresholds = (detail && detail.simulationThresholds) || { '10': { up: 100 } }
  const stockBase = Array.isArray(base.stock) ? base.stock : []
  const indexBase = Array.isArray(base.index) ? base.index : []
  let complete = true
  return rows.map((row, rowIndex) => {
    const stockValue = simulationInputValue(row.stock)
    const indexValue = simulationInputValue(row.index)
    if (stockValue == null || indexValue == null) complete = false
    if (!complete) {
      return { ...row, deviation10: '--', deviation30: '--', space: '--', deviation10Window: null, deviation30Window: null, deviationClass: '' }
    }
    const metricForWindow = window => {
      const stockWindowBase = base.stockByWindow && Array.isArray(base.stockByWindow[String(window)])
        ? base.stockByWindow[String(window)] : stockBase
      const indexWindowBase = base.indexByWindow && Array.isArray(base.indexByWindow[String(window)])
        ? base.indexByWindow[String(window)] : indexBase
      return bestDeviation(
        stockWindowBase.concat(rows.slice(0, rowIndex + 1).map(item => simulationInputValue(item.stock))),
        indexWindowBase.concat(rows.slice(0, rowIndex + 1).map(item => simulationInputValue(item.index))),
        window
      )
    }
    const metric10 = metricForWindow(10)
    const metric30 = metricForWindow(30)
    const deviation10 = metric10.value
    const deviation30 = metric30.value
    const display = value => value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
    const safetyMetrics = [
      { deviation: deviation10, downValue: metric10.downValue, up: thresholds['10'] && Number(thresholds['10'].up), down: thresholds['10'] && Number(thresholds['10'].down) },
      { deviation: deviation30, downValue: metric30.downValue, up: thresholds['30'] && Number(thresholds['30'].up), down: thresholds['30'] && Number(thresholds['30'].down) }
    ].filter(item => (item.deviation != null || item.downValue != null) && (Number.isFinite(item.up) || Number.isFinite(item.down)))
    const safety = safetyMetrics.length === 0
      ? '--'
      : safetyMetrics.some(item => (Number.isFinite(item.up) && item.deviation != null && item.deviation >= item.up) || (Number.isFinite(item.down) && item.downValue != null && item.downValue <= item.down))
        ? '已触发'
        : `${Math.min(...safetyMetrics.flatMap(item => [
          Number.isFinite(item.up) && item.deviation != null ? Math.max(0, item.up - item.deviation) : Infinity,
          Number.isFinite(item.down) && item.downValue != null ? Math.max(0, item.downValue - item.down) : Infinity
        ])).toFixed(2)}%`
    const currentDeviation = deviation10 == null ? (deviation30 == null ? 0 : deviation30) : deviation10
    const deviationClass = value => value == null ? '' : value > 0 ? 'risk' : value < 0 ? 'safe' : ''
    return {
      ...row,
      deviation10: display(deviation10),
      deviation30: display(deviation30),
      space: safety,
      deviation10Window: metric10.window,
      deviation30Window: metric30.window,
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

function presentWarnings(warnings = [], options = {}) {
  const colors = { base: '#B9D6F2', warning: '#F5C542', severe: '#D94A45', unknown: '#DCEBFA' }
  const nextDayRuleKeys = new Set((options.nextDayRules || []).map(item => String(item && item.ruleKey || '').trim()).filter(Boolean))
  const nextDayDirections = new Map((options.nextDayRules || [])
    .map(item => [String(item && item.ruleKey || '').trim(), String(item && item.direction || '').trim()])
    .filter(([key, direction]) => key && (direction === 'up' || direction === 'down')))
  const order = { '3 日偏离': 0, '10 日同向': 1, '10 日偏离': 2, '30 日偏离': 3 }
  const ruleKeyForTitle = title => ({
    '3 日偏离': 'ordinary_3d',
    '10 日同向': 'same_direction_10d',
    '10 日偏离': 'severe_10d',
    '30 日偏离': 'severe_30d'
  })[title] || ''
  const displayTone = (title, currentRisk, hasValue) => {
    const shortWindow = title === '3 日偏离' || title === '10 日同向'
    if (currentRisk || nextDayRuleKeys.has(ruleKeyForTitle(title))) return shortWindow ? 'warning' : 'severe'
    return hasValue ? 'base' : 'unknown'
  }
  return warnings.map((item) => {
    if (item.title === '10 日同向') {
      const up = Number(item.up || 0)
      const match = String(item.target || '').match(/(\d+(?:\.\d+)?)\s*次/)
      const limit = match ? Number(match[1]) : 0
      const down = Number(item.down || 0)
      const isUp = up >= down
      const count = isUp ? up : down
      const progress = limit > 0 ? Math.min(100, count / limit * 100) : 0
      const tone = limit <= 0 ? 'unknown' : progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe'
      const currentRisk = tone === 'triggered' || tone === 'warning'
      const labelTone = displayTone(item.title, currentRisk, limit > 0)
      const sign = isUp ? '+' : '-'
      const ringTone = labelTone === 'warning' || labelTone === 'severe' ? labelTone : tone === 'unknown' ? 'unknown' : 'base'
      return { ...item, currentText: `${sign}${count}次`, target: `阈值 ${isUp ? '+' : '-'}${limit}次`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${Math.max(0, limit - count)} 次`, ringText: tone === 'triggered' ? '已触及' : tone === 'unknown' ? '--' : `${Math.max(0, limit - count)}次`, ringValueClass: 'metric-ring-value-count', progress: Number(progress.toFixed(2)), tone, labelTone: ringTone, toneColor: colors[ringTone], valueClass: 'metric-current-count' }
    }
    const rawValue = String(item.value || '').replace('%', '')
    const value = Number.parseFloat(rawValue)
    const downValue = Number.parseFloat(String(item.downValue || '').replace('%', ''))
    if (!Number.isFinite(value) && !Number.isFinite(downValue)) {
      const labelTone = displayTone(item.title, false, false)
      return { ...item, currentText: '数据不足', remainingText: '暂不可判定', ringText: '--', ringValueClass: '', progress: 0, tone: 'unknown', labelTone, toneColor: colors[labelTone], valueClass: '' }
    }
    const thresholds = String(item.target || '').match(/[+-]?\d+(?:\.\d+)?/g) || []
    const up = Number.isFinite(Number(item.upThreshold)) ? Math.abs(Number(item.upThreshold)) : Math.abs(Number(thresholds[0] || 0))
    const downThreshold = Number.isFinite(Number(item.downThreshold)) ? Math.abs(Number(item.downThreshold)) : Math.abs(Number(thresholds[1] || 0))
    const upProgress = up > 0 && Number.isFinite(value) ? Math.max(0, value) / up * 100 : 0
    const downProgress = downThreshold > 0 && Number.isFinite(downValue) ? Math.max(0, Math.abs(Math.min(0, downValue))) / downThreshold * 100 : 0
    const progress = Math.min(100, Math.max(upProgress, downProgress))
    const triggered = Boolean(item.upTriggered || item.downTriggered)
    const tone = item.title === '3 日偏离'
      ? (triggered ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe')
      : (triggered ? 'triggered' : 'safe')
    // When the current snapshot is not triggered, a next-day candidate can
    // still be directional. Select that side's value/threshold so a downside
    // candidate is not rendered as an upside safety gap (for example
    // +192.36% alongside a red ring for a -70% rule).
    const forecastDirection = nextDayDirections.get(ruleKeyForTitle(item.title))
    const useUp = item.upTriggered
      ? true
      : (item.downTriggered
          ? false
          : (forecastDirection === 'down'
              ? false
              : (forecastDirection === 'up' ? true : (Number.isFinite(value) && value > 0))))
    const actual = useUp ? value : (Number.isFinite(downValue) && downValue < 0 ? downValue : (Number.isFinite(value) ? value : downValue))
    const positive = Number.isFinite(actual) && actual >= 0
    const selectedThreshold = positive ? up : downThreshold
    const actualText = Number.isFinite(actual) ? `${actual >= 0 ? '+' : ''}${actual.toFixed(2)}%` : '--'
    const remaining = Math.max(0, selectedThreshold - Math.abs(Number.isFinite(actual) ? actual : 0))
    const currentRisk = tone === 'triggered' || tone === 'warning'
    const labelTone = displayTone(item.title, currentRisk, Number.isFinite(actual))
    const ringTone = labelTone === 'warning' || labelTone === 'severe' ? labelTone : 'base'
    return { ...item, currentText: actualText, target: `阈值 ${positive ? '+' : '-'}${selectedThreshold}%`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${remaining.toFixed(2)}%`, ringText: tone === 'triggered' ? '已触及' : `${remaining.toFixed(2)}%`, ringValueClass: 'metric-ring-value-percent', progress: Number(progress.toFixed(2)), tone, labelTone: ringTone, toneColor: colors[ringTone], valueClass: actualText.length >= 8 ? 'metric-current-compact' : '' }
  }).sort((left, right) => (order[left.title] ?? 99) - (order[right.title] ?? 99))
}

Page({
  data: {
    query: '', suggestions: [], suggestionHint: false, suggestionTotal: 0, selected: null, detail: null, recentStocks: [],
    detailLoading: false, detailError: '', searchError: '', searchFocused: false, dataUpdatedAt: '',
    futureTradeDates: [], nextDayForecastLine: '', nextDayForecastText: '正在核对下一交易日预测…',
    dayOptions: ['10'], simulationExpanded: false, visibleDays: 2,
    inputRows: createInputRows(2), matrixGroups: buildMatrixGroups(createInputRows(2))
  },
  onLoad(options) {
    this.setData({ recentStocks: wx.getStorageSync(STOCK_HISTORY_KEY) || [] })
    if (options.ts_code) this.loadStockByCode(options.ts_code)
  },
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
  chooseRecent(event) {
    const stock = this.data.recentStocks[Number(event.currentTarget.dataset.index)]
    if (stock) this.chooseStock(stock)
  },
  clearHistory() {
    wx.removeStorageSync(STOCK_HISTORY_KEY)
    this.setData({ recentStocks: [] })
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
    const recentStocks = [{ ts_code: stock.ts_code, symbol: stock.symbol || stock.ts_code.split('.')[0], name: stock.name || stock.ts_code }, ...(this.data.recentStocks || []).filter(item => item.ts_code !== stock.ts_code)].slice(0, 5)
    wx.setStorageSync(STOCK_HISTORY_KEY, recentStocks)
    this.setData({ recentStocks })
    const inputRows = createInputRows(2, this.data.futureTradeDates)
    this.detailRequestId = (this.detailRequestId || 0) + 1
    const requestId = this.detailRequestId
    this.setData({ selected: stock, detail: null, detailLoading: true, detailError: '', dataUpdatedAt: '', nextDayForecastLine: '', nextDayForecastText: '正在核对下一交易日预测…', query: stock.name === '正在加载' ? stock.ts_code : `${stock.name} ${stock.symbol}`, suggestions: [], suggestionHint: false, inputRows, matrixGroups: buildMatrixGroups(inputRows), simulationExpanded: false, visibleDays: 2 })
    Promise.all([
      getStockDetail(stock.ts_code),
      // 详情页读取未去杂的监管快照；监控接口失败不影响行情详情展示。
      getMonitor({ status: 'current', type: 'all' }).catch(() => ({ items: [] }))
    ]).then(([detail, monitorSnapshot]) => {
      if (requestId !== this.detailRequestId) return
      const monitoring = detail.monitoring || findStockMonitoring(monitorSnapshot.items, detail.ts_code || stock.ts_code)
      const detailWithMonitoring = monitoring
        ? { ...detail, monitoring, monitoringStatus: 'active', monitoringStatusDisplay: '监管中' }
        : detail
      const futureTradeDates = detailWithMonitoring.futureTradeDates || []
      const rows = calculateSimulationRows(createInputRows(2, futureTradeDates), detailWithMonitoring)
      const displayDetail = {
        ...detailWithMonitoring,
        statusDisplay: detailWithMonitoring.statusDisplay || (detailWithMonitoring.status === '正常' ? '状态安全' : '状态异常'),
        marketStatusDisplay: detailWithMonitoring.marketStatusDisplay || (detailWithMonitoring.statusReason ? `已触发 · ${detailWithMonitoring.statusReason}` : '当前行情规则未触发'),
        change: detailWithMonitoring.change == null || detailWithMonitoring.change === '' ? '--' : String(detailWithMonitoring.change),
        changeClass: changeClass(detailWithMonitoring.change),
        dataQuality: detailWithMonitoring.dataQuality || {},
        warnings: presentWarnings(detailWithMonitoring.warnings, {
          nextDayRules: detailWithMonitoring.nextDayForecast && detailWithMonitoring.nextDayForecast.available
            ? (detailWithMonitoring.nextDayForecast.rules || [])
            : []
        }),
        alerts: presentAlerts(detailWithMonitoring.alerts),
        monitoringSummary: formatMonitoringSummary(monitoring && {
          ...monitoring,
          ts_code: monitoring.ts_code || detailWithMonitoring.ts_code || stock.ts_code,
          symbol: monitoring.symbol || detailWithMonitoring.symbol || stock.symbol
        })
      }
      const statusSummary = summaryStatus(displayDetail)
      displayDetail.summaryStatus = statusSummary.text
      displayDetail.summaryTone = statusSummary.tone
      const dataUpdatedAt = formatUpdatedAt((detailWithMonitoring.dataQuality || {}).quoteUpdatedAt || detailWithMonitoring.tradeDate)
      const recentStocks = [{ ts_code: detailWithMonitoring.ts_code || stock.ts_code, symbol: detailWithMonitoring.symbol || stock.symbol || stock.ts_code.split('.')[0], name: detailWithMonitoring.name || stock.name || stock.ts_code }, ...(this.data.recentStocks || []).filter(item => item.ts_code !== (detailWithMonitoring.ts_code || stock.ts_code))].slice(0, 5)
      wx.setStorageSync(STOCK_HISTORY_KEY, recentStocks)
      this.setData({ selected: { ...stock, ...detailWithMonitoring }, detail: displayDetail, detailLoading: false, detailError: '', dataUpdatedAt, recentStocks, futureTradeDates, inputRows: rows, matrixGroups: buildMatrixGroups(rows), query: `${detailWithMonitoring.name} ${detailWithMonitoring.symbol}` })
      const forecast = buildNextDayForecast(detailWithMonitoring.nextDayForecast || {})
      this.setData({ nextDayForecastLine: forecast.line, nextDayForecastText: forecast.text })
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
if (typeof module !== 'undefined') module.exports = { bestDeviation, buildMatrixGroups, buildNextDayForecast, calculateSimulationRows, createInputRows, presentWarnings, summaryStatus, formatMonthDay, monitoringRuleLabel, formatMonitoringSummary, remainingMonitoringDays, findStockMonitoring }
