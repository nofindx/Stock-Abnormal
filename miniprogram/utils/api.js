// 小程序 API 服务层：统一路径、参数和响应数据，不让页面直接拼接接口。
const { request } = require('./request')

function searchStocks(query) {
  return request({ url: '/api/stocks/search', data: { q: query }, timeout: 6000 }).then((response) => response.data || { items: [], hasMore: false })
}

function getStockDetail(tsCode) {
  return request({ url: '/api/stocks/detail', data: { ts_code: tsCode }, timeout: 30000 }).then((response) => response.data || null)
}

function getMonitor(options = {}) {
  return request({ url: '/api/monitor', data: options, timeout: 8000 }).then((response) => response.data || { items: [], updatedAt: '' })
}

function getPredictions(scope) {
  return request({ url: '/api/predictions', data: { scope }, timeout: 8000 }).then((response) => response.data || { items: [], quoteUpdatedAt: '' })
}

function refreshPredictions(scope) {
  return request({ url: '/api/predictions/refresh', method: 'POST', data: { scope }, timeout: 10000, retry: 0 }).then((response) => response.data || { items: [], quoteUpdatedAt: '' })
}

module.exports = { searchStocks, getStockDetail, getMonitor, getPredictions, refreshPredictions }
