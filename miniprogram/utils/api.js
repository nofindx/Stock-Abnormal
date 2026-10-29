// 小程序 API 服务层：统一路径、参数和响应数据，不让页面直接拼接接口。
const { request } = require('./request')

function searchStocks(query) {
  return request({ url: '/api/stocks/search', data: { q: query }, timeout: 6000 }).then((response) => response.data || { items: [], hasMore: false })
}

function getStockDetail(tsCode) {
  return getVNextContext('stock', 'current', tsCode).then((context) => request({
    url: '/api/v2/stocks/detail',
    data: { security_id: tsCode, read_token: context.read_token.value },
    timeout: 30000
  })).then((response) => response.data || null)
}

function getMonitor(options = {}) {
  return request({ url: '/api/monitor', data: options, timeout: 8000 }).then((response) => response.data || { items: [], updatedAt: '' })
}

function getPredictions(scope) {
  const role = scope === 'next_day' ? 'next' : 'current'
  return getVNextContext('prediction', role).then((context) => getVNextPredictions(context.read_token.value, role))
}

function refreshPredictions(scope) {
  const role = scope === 'next_day' ? 'next' : 'current'
  return getVNextContext('prediction', role).then((context) => request({
    url: '/api/v2/realtime/refresh', method: 'POST',
    data: { scope, read_token: context.read_token.value }, timeout: 10000, retry: 0
  })).then((response) => response.data || { items: [], quoteUpdatedAt: '' })
}

// 仅供需要原始盘中行情的页面使用；后端不保存本次报价。
function getRealtimeQuotes(tsCodes) {
  return request({ url: '/api/quotes/realtime', method: 'POST', data: { ts_codes: tsCodes }, timeout: 8000, retry: 0 }).then((response) => response.data || { quotes: {}, persisted: false })
}

// vNext opt-in contract. Legacy pages continue using the frozen endpoints
// until the cloud environment has passed the S1/S2 release gates.
function getVNextContext(page, role = 'current', securityId = '') {
  const entryId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return request({ url: '/api/v2/context', data: { page, role, security_id: securityId, entry_id: entryId }, timeout: 8000 }).then((response) => response.data || null)
}

function getVNextPredictions(readToken, role = 'current') {
  return request({ url: '/api/v2/predictions', data: { read_token: readToken, role }, timeout: 8000 }).then((response) => response.data || { items: [] })
}

module.exports = { searchStocks, getStockDetail, getMonitor, getPredictions, refreshPredictions, getRealtimeQuotes, getVNextContext, getVNextPredictions }
