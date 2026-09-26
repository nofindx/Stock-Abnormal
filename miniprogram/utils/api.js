// 小程序 API 服务层：统一路径、参数和响应数据，不让页面直接拼接接口。
const { request } = require('./request')

function searchStocks(query) {
  return request({ url: '/api/stocks/search', data: { q: query }, timeout: 6000 }).then((response) => response.data || { items: [], hasMore: false })
}

function getStockDetail(tsCode) {
  return request({ url: '/api/stocks/detail', data: { ts_code: tsCode }, timeout: 15000 }).then((response) => response.data || null)
}

function getStockAnnouncements(tsCode) {
  return request({ url: '/api/stocks/announcements', data: { ts_code: tsCode }, timeout: 15000 }).then((response) => response.data || { items: [], available: false })
}

function getMonitor(options = {}) {
  return request({ url: '/api/monitor', data: options, timeout: 15000 }).then((response) => response.data || { items: [], updatedAt: '' })
}

function getPredictions(scope) {
  return request({ url: '/api/predictions', data: { scope }, timeout: 30000 }).then((response) => response.data || { items: [], updatedAt: '' })
}

function refreshPredictions(scope) {
  return request({ url: '/api/predictions/refresh', method: 'POST', data: { scope }, timeout: 60000, retry: 0 }).then((response) => response.data || { items: [], updatedAt: '' })
}

module.exports = { searchStocks, getStockDetail, getStockAnnouncements, getMonitor, getPredictions, refreshPredictions }
