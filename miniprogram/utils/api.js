// 小程序 API 服务层：统一路径、参数和响应数据，不让页面直接拼接接口。
const { request } = require('./request')

function searchStocks(query) {
  return request({ url: '/api/stocks/search', data: { q: query }, timeout: 6000 }).then((response) => response.data || { items: [], hasMore: false })
}

function getStockDetail(tsCode) {
  return readLatestS1('stock', 'current', tsCode, (token) => request({
    url: '/api/v2/stocks/detail',
    data: { security_id: tsCode, read_token: token },
    timeout: 30000
  }).then(response => response.data || null))
}

function readLatestS1(page, role, code, readLegacy) {
  const acquire = () => getVNextContext(page, role, code, { includeS1: true }).then(context => {
    if (!context || !context.read_token || !context.read_token.value) throw new Error('读取上下文不可用')
    if (!Object.prototype.hasOwnProperty.call(context, 's1')) return readLegacy(context.read_token.value)
    const s1 = context.s1
    if (!s1 || !s1.read_token || s1.read_token.value !== context.read_token.value ||
        ['dataset_id', 'dataset_version', 'pool_hash', 'pool_version', 'rule_version', 'time_context_id'].some(key => s1[key] !== context[key]) ||
        page === 'stock' && s1.ts_code !== code) throw new Error('读取数据与授权范围不一致')
    return s1
  })
  return acquire().catch(error => {
    if (error.code !== 40902) throw error
    return acquire()
  })
}

function withReadContext(page, role, code, action, readResult) {
  const first = readResult && readResult.read_token && readResult.read_token.value
  const acquire = () => getVNextContext(page, role, code).then(context => context.read_token.value)
  return (first ? Promise.resolve(first) : acquire()).then(action).catch(error => {
    if (error.code !== 40902) throw error
    // A time/version expiry is recovered once with a new authorized S1.
    return acquire().then(action)
  })
}

function refreshStockDetail(tsCode, readResult, options = {}) {
  return refreshLatestRead(() => getStockDetail(tsCode), fresh => refreshStockQuote(tsCode, fresh), readResult, options)
}

function refreshStockQuote(tsCode, readResult) {
  return withReadContext('stock', 'current', tsCode, token => request({
    url: '/api/v2/realtime/refresh', method: 'POST',
    data: { page: 'stock', security_id: tsCode, read_token: token }, timeout: 15000, retry: 0
  }).then(response => waitRealtimeJob(response, token)), readResult).then(response => response.data)
}

function waitRealtimeJob(response, token, attempt = 0) {
  const data = response.data || {}
  if (data.refresh_state !== 'queued' || !data.job_id) return Promise.resolve(response)
  if (attempt >= 24) {
    const error = new Error('实时行情仍在刷新，请稍后重试')
    error.code = 20201
    return Promise.reject(error)
  }
  return new Promise(resolve => setTimeout(resolve, Math.max(250, Math.min(1000, data.retry_after_ms || 500))))
    .then(() => request({ url: `/api/v2/jobs/${data.job_id}`, data: { read_token: token }, timeout: 8000, retry: 0 }))
    .then(next => waitRealtimeJob(next, token, attempt + 1))
}

function getMonitor(options = {}) {
  return request({ url: '/api/monitor', data: options, timeout: 8000 }).then((response) => response.data || { items: [], updatedAt: '' })
}

function getPredictions(scope) {
  const role = scope === 'next_day' ? 'next' : 'current'
  return readLatestS1('prediction', role, '', token => getVNextPredictions(token, role))
}

function getNextPredictionStatus() {
  return request({ url: '/api/predictions/next-status', timeout: 8000, retry: 0 }).then(response => response.data)
}

function refreshPredictions(scope, readResult, options = {}) {
  return refreshLatestRead(() => getPredictions(scope), fresh => refreshPredictionQuotes(scope, fresh), readResult, { ...options, role: scope === 'next_day' ? 'next' : 'current' })
}

function refreshPredictionQuotes(scope, readResult) {
  const role = scope === 'next_day' ? 'next' : 'current'
  return withReadContext('prediction', role, '', token => request({
    url: '/api/v2/realtime/refresh', method: 'POST',
    data: { scope, read_token: token }, timeout: 15000, retry: 0
  }).then(response => waitRealtimeJob(response, token)), readResult).then((response) => response.data || { items: [], quoteUpdatedAt: '' })
}

// First-open S2 reuses the S1 just rendered. Every explicit manual refresh
// acquires current S1 even while the old token is still valid; quotes stay
// limited to that newly published scope, never a user-triggered market scan.
function refreshLatestRead(readLatest, refreshQuotes, previous, options) {
  if (options.reuseContext) return refreshQuotes(previous)
  return readLatest().then(fresh => {
    if (!fresh) throw new Error('最新数据暂不可用')
    if (fresh.realtimeRefreshAllowed === false || fresh.nextDayAvailable === false && options.role === 'next') return fresh
    if (typeof options.onS1 === 'function') options.onS1(fresh)
    return refreshQuotes(fresh).catch(() => ({
      ...fresh, refreshStatus: 'failed', refresh_state: 'failed', result_state: 'stale',
      reasons: ['REALTIME_REFRESH_FAILED']
    }))
  })
}

// 仅供需要原始盘中行情的页面使用；后端不保存本次报价。
function getRealtimeQuotes(tsCodes) {
  return request({ url: '/api/quotes/realtime', method: 'POST', data: { ts_codes: tsCodes }, timeout: 8000, retry: 0 }).then((response) => response.data || { quotes: {}, persisted: false })
}

// S1 pins the published pool; S2 updates only that authorized pool's quotes.
function getVNextContext(page, role = 'current', securityId = '', options = {}) {
  const entryId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const data = { page, role, security_id: securityId, entry_id: entryId }
  if (options.includeS1) data.include_s1 = '1'
  return request({ url: '/api/v2/context', data, timeout: 8000 }).then((response) => response.data || null)
}

function getVNextPredictions(readToken, role = 'current') {
  return request({ url: '/api/v2/predictions', data: { read_token: readToken, role }, timeout: 8000 }).then((response) => response.data || { items: [] })
}

module.exports = { searchStocks, getStockDetail, refreshStockDetail, getMonitor, getPredictions, getNextPredictionStatus, refreshPredictions, getRealtimeQuotes, getVNextContext, getVNextPredictions }
