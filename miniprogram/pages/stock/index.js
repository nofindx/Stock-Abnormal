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
    badgeText: isDelistingMonitoring(monitoring) ? '退市整理期' : '已被重点监管',
    ruleText: monitoringRuleLabel(monitoring),
    remainingText: remainingMonitoringDays(monitoring, today)
  }
}

function isDelistingMonitoring(item = {}) {
  return String(item.riskTone || '') === 'delisting-warning'
    || /退市整理|退市倒计时/.test(`${item.monitorType || ''} ${item.ruleText || ''} ${item.monitorReason || ''}`)
}

function monitoringPriority(item = {}) {
  if (isDelistingMonitoring(item)) return 5
  return { 'severe-30d': 4, 'severe-10d': 3, ordinary: 1 }[String(item.riskTone || '')] || 0
}

function findStockMonitoring(items = [], tsCode = '') {
  const code = String(tsCode || '').split('.')[0]
  return (items || [])
    .filter(item => item && item.active !== false && String(item.ts_code || item.symbol || '').split('.')[0] === code)
    .sort((left, right) => monitoringPriority(right) - monitoringPriority(left) || String(right.monitorEndDate || '').localeCompare(String(left.monitorEndDate || '')))[0] || null
}

function selectStockMonitoring(detailMonitoring, snapshotItems = [], tsCode = '', fallbackSymbol = '') {
  const stockCode = String(tsCode || '')
  const stockSymbol = stockCode.split('.')[0]
  const facts = detailMonitoring
    ? [{ ...detailMonitoring, ts_code: detailMonitoring.ts_code || stockCode }]
    : []
  const matchingSnapshotFacts = (snapshotItems || [])
    .filter(item => item && String(item.ts_code || item.symbol || '').split('.')[0] === stockSymbol)
  return findStockMonitoring([...facts, ...matchingSnapshotFacts].map(item => ({
    ...item,
    ts_code: item.ts_code || stockCode,
    symbol: item.symbol || fallbackSymbol
  })), stockCode)
}

const STOCK_HISTORY_KEY = 'stockSearchHistory'

function normalizeTradeDate(value) {
  return String(value || '').replace(/[^\d]/g, '').slice(0, 8)
}

const WARNING_RULE_KEYS = {
  '3 日偏离': 'ordinary_3d',
  '10 日同向': 'same_direction_10d',
  '10 日偏离': 'severe_10d',
  '30 日偏离': 'severe_30d'
}

function assessNextDayRules(rules = [], monitoringActive = false) {
  const candidates = rules.filter(item => item && WARNING_RULE_KEYS_BY_KEY.has(String(item.ruleKey || '')))
  const ruleKeys = new Set(candidates.map(item => String(item.ruleKey)))
  if (!ruleKeys.size) return { text: '暂无异动风险', tone: 'safe', rules: [] }

  const severeKeys = new Set(['same_direction_10d', 'severe_10d', 'severe_30d'])
  const warningKeys = new Set(['ordinary_3d'])
  // 规则级别由指标本身决定，监管状态不改变 3 日普通异动的级别。
  // 若同时有严重候选，摘要优先列严重规则；否则列普通 3 日候选。
  if (Array.from(ruleKeys).some(key => severeKeys.has(key))) {
    return { text: '存在异动风险', tone: 'risk', rules: candidates.filter(item => severeKeys.has(String(item.ruleKey))) }
  }
  return { text: '存在警告风险', tone: 'warning', rules: candidates.filter(item => warningKeys.has(String(item.ruleKey))) }
}

const WARNING_RULE_KEYS_BY_KEY = new Set(Object.values(WARNING_RULE_KEYS))

function formatRuleThreshold(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return Number.isInteger(number) ? String(number) : number.toFixed(2)
}

function currentSevereRuleTexts(warnings = []) {
  const texts = []
  warnings.forEach(item => {
    const title = String(item && item.title || '')
    if (title === '10 日同向') {
      const match = String(item.target || '').match(/(\d+(?:\.\d+)?)\s*次/)
      const threshold = match ? Number(match[1]) : 0
      const up = Number(item.up || 0)
      const down = Number(item.down || 0)
      const isUp = up >= down
      const count = isUp ? up : down
      if (threshold > 0 && (item.upTriggered || item.downTriggered || count >= threshold)) {
        texts.push(`连续10个交易日内出现${threshold}次${isUp ? '上涨' : '下跌'}同向异常波动`)
      }
      return
    }
    if (title !== '10 日偏离' && title !== '30 日偏离') return
    const window = title.startsWith('30') ? 30 : 10
    const directions = []
    if (item.upTriggered) directions.push({ label: '涨幅', value: item.upThreshold })
    if (item.downTriggered) directions.push({ label: '跌幅', value: Math.abs(Number(item.downThreshold)) })
    directions.forEach(direction => {
      const threshold = formatRuleThreshold(direction.value)
      if (threshold) texts.push(`连续${window}个交易日内收盘价${direction.label}偏离值累计达到 ${direction.label === '涨幅' ? '+' : '-'}${threshold}%`)
    })
  })
  return [...new Set(texts)]
}

function buildNextDayForecast(forecast = {}, monitoringActive = false, currentSevereRules = [], currentTradeDate = '', ruleFactsStatus = '') {
  const candidates = Array.isArray(forecast.rules) ? forecast.rules : []
  const assessment = assessNextDayRules(candidates, monitoringActive)
  const ruleText = [...new Set(assessment.rules.map(item => String(item.rule || '').trim()).filter(Boolean))].join('；')
  const unavailable = () => ({
    line: '',
    text: '次日预测暂不可用',
    summaryStatus: '未知状态',
    summaryTone: 'unknown'
  })
  if (['pending_verification', 'partial', 'unavailable', 'not_configured', 'unknown'].includes(String(ruleFactsStatus || ''))) {
    return unavailable()
  }
  if (typeof forecast.available !== 'boolean') return unavailable()
  const tradeDate = normalizeTradeDate(forecast.baseTradeDate)
  const nextTradeDate = normalizeTradeDate(forecast.targetTradeDate)
  const currentDate = normalizeTradeDate(currentTradeDate)
  if (!tradeDate || !nextTradeDate || nextTradeDate <= tradeDate) return unavailable()
  if (currentDate && nextTradeDate < currentDate) return unavailable()
  if (!currentDate && currentSevereRules.length) return unavailable()
  // Current severe metrics may include a live quote. Keep that state only while
  // the forecast target is still a future session; once target day arrives,
  // the target-day forecast decides the banner regardless of the old trigger.
  const currentSevereIsForFutureSession = Boolean(currentDate && nextTradeDate > currentDate)
  if (currentSevereIsForFutureSession && currentSevereRules.length) {
    const futureLine = forecast.available && nextTradeDate
      ? `次日预测（按 ${tradeDate.slice(0, 4)}-${tradeDate.slice(4, 6)}-${tradeDate.slice(6)} 收盘数据预测 ${nextTradeDate.slice(0, 4)}-${nextTradeDate.slice(4, 6)}-${nextTradeDate.slice(6)}）`
      : '次日预测'
    const futureUnavailable = !forecast.available || !nextTradeDate || candidates.some(item => !String(item.rule || '').trim())
    const futureText = futureUnavailable
      ? '次日预测暂不可用'
      : assessment.tone === 'safe'
        ? '未发现下一交易日可触发的异动规则'
        : `次日可能触发：${ruleText}`
    return {
      line: `按 ${tradeDate.slice(0, 4)}-${tradeDate.slice(4, 6)}-${tradeDate.slice(6)} 收盘数据预测`,
      text: `已触发：${currentSevereRules.join('；')}`,
      notice: '将进入重点监控池，具体以官方公告为准。',
      futureLine,
      futureText,
      summaryStatus: '当日已触发严重异动',
      summaryTone: 'risk',
      currentTriggered: true
    }
  }
  if (!forecast.available || !nextTradeDate) return unavailable()
  const line = `按 ${tradeDate.slice(0, 4)}-${tradeDate.slice(4, 6)}-${tradeDate.slice(6)} 收盘数据预测 ${nextTradeDate.slice(0, 4)}-${nextTradeDate.slice(4, 6)}-${nextTradeDate.slice(6)}`
  if (candidates.some(item => !String(item.rule || '').trim())) return unavailable()
  const text = assessment.tone === 'safe'
    ? '未发现下一交易日可触发的异动规则'
    : `次日可能触发：${ruleText}`
  return {
    line,
    text,
    summaryStatus: assessment.text,
    summaryTone: assessment.tone
  }
}

function resolveStatusBanner(forecast = {}, monitoringSummary = null) {
  if (forecast.currentTriggered) {
    return { summaryStatus: forecast.summaryStatus, summaryTone: forecast.summaryTone, statusClass: 'current-triggered' }
  }
  return { summaryStatus: forecast.summaryStatus, summaryTone: forecast.summaryTone, statusClass: forecast.summaryTone }
}

function summaryStatus(detail) {
  const monitoringActive = Boolean(detail && detail.monitoring && detail.monitoring.active !== false)
  const ruleFactsStatus = detail && detail.dataQuality && detail.dataQuality.ruleFacts && detail.dataQuality.ruleFacts.status
  const assessment = buildNextDayForecast(detail && detail.nextDayForecast || {}, monitoringActive, [], '', ruleFactsStatus)
  return { text: assessment.summaryStatus, tone: assessment.summaryTone }
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
  const forecastRules = options.nextDayRules || []
  const nextDayRuleKeys = new Set(forecastRules.map(item => String(item && item.ruleKey || '').trim()).filter(Boolean))
  const order = { '3 日偏离': 0, '10 日同向': 1, '10 日偏离': 2, '30 日偏离': 3 }
  const ruleKeyForTitle = title => ({
    '3 日偏离': 'ordinary_3d',
    '10 日同向': 'same_direction_10d',
    '10 日偏离': 'severe_10d',
    '30 日偏离': 'severe_30d'
  })[title] || ''
  const displayTone = (title, currentRisk, hasValue) => {
    const ruleKey = ruleKeyForTitle(title)
    if (currentRisk || nextDayRuleKeys.has(ruleKey)) return ruleKey === 'ordinary_3d' ? 'warning' : 'severe'
    return hasValue ? 'base' : 'unknown'
  }
  const presented = warnings.map((item) => {
    if (item.title === '10 日同向') {
      const up = Number(item.up || 0)
      const match = String(item.target || '').match(/(\d+(?:\.\d+)?)\s*次/)
      const limit = match ? Number(match[1]) : 0
      const down = Number(item.down || 0)
      const isUp = up >= down
      const count = isUp ? up : down
      const pending = Array.isArray(item.pendingEvents) && item.pendingEvents.length > 0
      const progress = limit > 0 ? Math.min(100, count / limit * 100) : 0
      const tone = limit <= 0 ? 'unknown' : progress >= 100 ? 'triggered' : progress >= 66.6667 ? 'warning' : 'safe'
      const currentRisk = tone === 'triggered' || tone === 'warning'
      const labelTone = displayTone(item.title, currentRisk, limit > 0)
      const sign = isUp ? '+' : '-'
      const ringTone = labelTone === 'warning' || labelTone === 'severe' ? labelTone : tone === 'unknown' ? 'unknown' : 'base'
      return { ...item, currentText: `${sign}${count}次`, currentNumber: `${sign}${count}`, currentUnit: '次', currentFontSize: 20, target: `阈值 ${isUp ? '+' : '-'}${limit}次${pending ? ' · 待核验' : ''}`, remainingText: pending ? '存在待核验公告' : (tone === 'triggered' ? '已触及' : `还差 ${Math.max(0, limit - count)} 次`), ringText: pending ? '待核验' : (tone === 'triggered' ? '已触及' : tone === 'unknown' ? '--' : `${Math.max(0, limit - count)}次`), ringValueClass: 'metric-ring-value-count', progress: Number(progress.toFixed(2)), tone: pending ? 'unknown' : tone, labelTone: pending ? 'unknown' : ringTone, toneColor: colors[pending ? 'unknown' : ringTone], valueClass: 'metric-current-count' }
    }
    const rawValue = String(item.value || '').replace('%', '')
    const value = Number.parseFloat(rawValue)
    const downValue = Number.parseFloat(String(item.downValue || '').replace('%', ''))
    if (!Number.isFinite(value) && !Number.isFinite(downValue)) {
      const labelTone = displayTone(item.title, false, false)
      return { ...item, currentText: '数据不足', currentNumber: '数据不足', currentUnit: '', currentFontSize: 22, remainingText: '暂不可判定', ringText: '--', ringValueClass: '', progress: 0, tone: 'unknown', labelTone, toneColor: colors[labelTone], valueClass: '' }
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
    // The current displayed deviation is selected from current market data,
    // never from the direction of a next-day forecast candidate. Match the
    // ring's progress rule so the value, threshold, and ring describe the same side.
    const useUp = upProgress >= downProgress
    const actual = useUp
      ? (Number.isFinite(value) ? value : downValue)
      : (Number.isFinite(downValue) ? downValue : value)
    const positive = Number.isFinite(actual) && actual >= 0
    const selectedThreshold = positive ? up : downThreshold
    const actualText = Number.isFinite(actual) ? `${actual >= 0 ? '+' : ''}${actual.toFixed(2)}%` : '--'
    const remaining = Math.max(0, selectedThreshold - Math.abs(Number.isFinite(actual) ? actual : 0))
    const currentRisk = tone === 'triggered' || tone === 'warning'
    const labelTone = displayTone(item.title, currentRisk, Number.isFinite(actual))
    const ringTone = labelTone === 'warning' || labelTone === 'severe' ? labelTone : 'base'
    const numberText = actualText.endsWith('%') ? actualText.slice(0, -1) : actualText
    const currentFontSize = numberText.length >= 8 ? 21 : numberText.length >= 7 ? 24 : numberText.length >= 6 ? 27 : 30
    return { ...item, currentText: actualText, currentNumber: numberText, currentUnit: '%', currentFontSize, target: `阈值 ${positive ? '+' : '-'}${selectedThreshold}%`, remainingText: tone === 'triggered' ? '已触及' : `还差 ${remaining.toFixed(2)}%`, ringText: tone === 'triggered' ? '已触及' : `${remaining.toFixed(2)}%`, ringValueClass: 'metric-ring-value-percent', progress: Number(progress.toFixed(2)), tone, labelTone: ringTone, toneColor: colors[ringTone], valueClass: '' }
  }).sort((left, right) => (order[left.title] ?? 99) - (order[right.title] ?? 99))
  if (!options.forceUnknown) return presented
  return presented.map(item => ({
    ...item,
    target: /待核验/.test(String(item.target || '')) ? item.target : `${item.target} · 待核验`,
    remainingText: '存在待核验公告',
    ringText: '待核验',
    progress: 0,
    tone: 'unknown',
    labelTone: 'unknown',
    toneColor: colors.unknown
  }))
}

Page({
  data: {
    query: '', suggestions: [], suggestionHint: false, suggestionTotal: 0, selected: null, detail: null, recentStocks: [],
    detailLoading: false, detailError: '', searchError: '', searchFocused: false, dataUpdatedAt: '',
    futureTradeDates: [], nextDayForecastLine: '', nextDayForecastText: '正在核对下一交易日预测…', nextDayForecastNotice: '',
    dayOptions: ['10'], simulationExpanded: false, visibleDays: 2,
    inputRows: createInputRows(2), matrixGroups: buildMatrixGroups(createInputRows(2))
  },
  onLoad(options) {
    this.setData({ recentStocks: (wx.getStorageSync(STOCK_HISTORY_KEY) || []).slice(0, 8) })
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
    const recentStocks = [{ ts_code: stock.ts_code, symbol: stock.symbol || stock.ts_code.split('.')[0], name: stock.name || stock.ts_code }, ...(this.data.recentStocks || []).filter(item => item.ts_code !== stock.ts_code)].slice(0, 8)
    wx.setStorageSync(STOCK_HISTORY_KEY, recentStocks)
    this.setData({ recentStocks })
    const inputRows = createInputRows(2, this.data.futureTradeDates)
    this.detailRequestId = (this.detailRequestId || 0) + 1
    const requestId = this.detailRequestId
    this.setData({ selected: stock, detail: null, detailLoading: true, detailError: '', dataUpdatedAt: '', nextDayForecastLine: '', nextDayForecastText: '正在核对下一交易日预测…', nextDayForecastNotice: '', query: stock.name === '正在加载' ? stock.ts_code : `${stock.name} ${stock.symbol}`, suggestions: [], suggestionHint: false, inputRows, matrixGroups: buildMatrixGroups(inputRows), simulationExpanded: false, visibleDays: 2 })
    Promise.all([
      getStockDetail(stock.ts_code),
      // 详情页读取未去杂的监管快照；监控接口失败不影响行情详情展示。
      getMonitor({ status: 'current', type: 'all' }).catch(() => ({ items: [] }))
    ]).then(([detail, monitorSnapshot]) => {
      if (requestId !== this.detailRequestId) return
      const stockCode = detail.ts_code || stock.ts_code
      // The endpoint may choose a severe event as its primary monitoring fact;
      // keep all current facts available for the display label so delisting
      // management is not mislabeled as severe monitoring on retired stocks.
      const monitoring = selectStockMonitoring(
        detail.monitoring,
        monitorSnapshot.items,
        stockCode,
        detail.symbol || stock.symbol
      )
      const detailWithMonitoring = monitoring
        ? { ...detail, monitoring, monitoringStatus: 'active', monitoringStatusDisplay: '监管中' }
        : detail
      const futureTradeDates = detailWithMonitoring.futureTradeDates || []
      const rows = calculateSimulationRows(createInputRows(2, futureTradeDates), detailWithMonitoring)
      const ruleFactsStatus = String((detailWithMonitoring.dataQuality && detailWithMonitoring.dataQuality.ruleFacts || {}).status || 'unknown')
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
            : [],
          forceUnknown: ruleFactsStatus !== 'available'
        }),
        alerts: presentAlerts(detailWithMonitoring.alerts),
        monitoringSummary: formatMonitoringSummary(monitoring && {
          ...monitoring,
          ts_code: monitoring.ts_code || detailWithMonitoring.ts_code || stock.ts_code,
          symbol: monitoring.symbol || detailWithMonitoring.symbol || stock.symbol
        })
      }
      const currentSevereRules = currentSevereRuleTexts(detailWithMonitoring.warnings || [])
      const monitoringStartKey = dateKey(monitoring && monitoring.monitorStartDate)
      const detailTradeDateKey = dateKey(detailWithMonitoring.tradeDate)
      if (
        monitoring && /^severe-/.test(String(monitoring.riskTone || ''))
        && monitoringStartKey && detailTradeDateKey && monitoringStartKey > detailTradeDateKey
      ) {
        const announcedRule = monitoringRuleLabel({ ...monitoring, ts_code: detailWithMonitoring.ts_code || stock.ts_code })
        if (announcedRule && !currentSevereRules.includes(announcedRule)) currentSevereRules.push(announcedRule)
      }
      const forecast = buildNextDayForecast(
        detailWithMonitoring.nextDayForecast || {},
        Boolean(displayDetail.monitoringSummary),
        currentSevereRules,
        detailWithMonitoring.tradeDate,
        ruleFactsStatus
      )
      const statusSummary = { text: forecast.summaryStatus, tone: forecast.summaryTone }
      displayDetail.currentTriggered = forecast.currentTriggered
      displayDetail.futureForecastLine = forecast.futureLine || ''
      displayDetail.futureForecastText = forecast.futureText || ''
      Object.assign(displayDetail, resolveStatusBanner({ ...forecast, summaryStatus: statusSummary.text, summaryTone: statusSummary.tone }, displayDetail.monitoringSummary))
      const dataUpdatedAt = formatUpdatedAt((detailWithMonitoring.dataQuality || {}).quoteUpdatedAt || detailWithMonitoring.tradeDate)
      const recentStocks = [{ ts_code: detailWithMonitoring.ts_code || stock.ts_code, symbol: detailWithMonitoring.symbol || stock.symbol || stock.ts_code.split('.')[0], name: detailWithMonitoring.name || stock.name || stock.ts_code }, ...(this.data.recentStocks || []).filter(item => item.ts_code !== (detailWithMonitoring.ts_code || stock.ts_code))].slice(0, 8)
      wx.setStorageSync(STOCK_HISTORY_KEY, recentStocks)
      this.setData({ selected: { ...stock, ...detailWithMonitoring }, detail: displayDetail, detailLoading: false, detailError: '', dataUpdatedAt, recentStocks, futureTradeDates, inputRows: rows, matrixGroups: buildMatrixGroups(rows), query: `${detailWithMonitoring.name} ${detailWithMonitoring.symbol}`, nextDayForecastLine: forecast.line, nextDayForecastText: forecast.text, nextDayForecastNotice: forecast.notice || '' })
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
if (typeof module !== 'undefined') module.exports = { bestDeviation, buildMatrixGroups, buildNextDayForecast, calculateSimulationRows, createInputRows, presentWarnings, summaryStatus, formatMonthDay, monitoringRuleLabel, formatMonitoringSummary, remainingMonitoringDays, findStockMonitoring, selectStockMonitoring, currentSevereRuleTexts, resolveStatusBanner }
