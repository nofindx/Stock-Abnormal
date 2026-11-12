// 小程序 API 服务层：统一路径、参数和响应数据，不让页面直接拼接接口。
const { request } = require('./request')

function searchStocks(query) {
  return request({ url: '/api/stocks/search', data: { q: query }, timeout: 6000 }).then((response) => response.data || { items: [], hasMore: false })
}

function getStockDetail(tsCode) {
  return withReadContext('stock', 'current', tsCode, (token) => request({
    url: '/api/v2/stocks/detail',
    data: { security_id: tsCode, read_token: token },
    timeout: 30000
  })).then((response) => response.data || null)
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

function refreshStockDetail(tsCode, readResult) {
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
  return withReadContext('prediction', role, '', token => getVNextPredictions(token, role))
}

function getNextPredictionStatus() {
  return request({ url: '/api/predictions/next-status', timeout: 8000, retry: 0 }).then(response => response.data)
}

function refreshPredictions(scope, readResult) {
  const role = scope === 'next_day' ? 'next' : 'current'
  return withReadContext('prediction', role, '', token => request({
    url: '/api/v2/realtime/refresh', method: 'POST',
    data: { scope, read_token: token }, timeout: 15000, retry: 0
  }).then(response => waitRealtimeJob(response, token)), readResult).then((response) => response.data || { items: [], quoteUpdatedAt: '' })
}

// 仅供需要原始盘中行情的页面使用；后端不保存本次报价。
function getRealtimeQuotes(tsCodes) {
  return request({ url: '/api/quotes/realtime', method: 'POST', data: { ts_codes: tsCodes }, timeout: 8000, retry: 0 }).then((response) => response.data || { quotes: {}, persisted: false })
}

// S1 pins the published pool; S2 updates only that authorized pool's quotes.
function getVNextContext(page, role = 'current', securityId = '') {
  const entryId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return request({ url: '/api/v2/context', data: { page, role, security_id: securityId, entry_id: entryId }, timeout: 8000 }).then((response) => response.data || null)
}

function getVNextPredictions(readToken, role = 'current') {
  return request({ url: '/api/v2/predictions', data: { read_token: readToken, role }, timeout: 8000 }).then((response) => response.data || { items: [] })
}

module.exports = { searchStocks, getStockDetail, refreshStockDetail, getMonitor, getPredictions, getNextPredictionStatus, refreshPredictions, getRealtimeQuotes, getVNextContext, getVNextPredictions }
