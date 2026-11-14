// Share URLs identify a stock only. Inputs, grants and API read tokens never travel in URLs.
const STOCK_PAGE = 'pages/stock/index'
function stockCode(value) {
  const code = String(value || '').toUpperCase()
  return /^\d{6}\.(SH|SZ|BJ)$/.test(code) ? code : ''
}
function shareEntry(code) {
  const valid = stockCode(code)
  return valid ? `${STOCK_PAGE}?ts_code=${encodeURIComponent(valid)}&from=stock_share` : STOCK_PAGE
}
function parseShareEntry(query = {}) {
  query = query || {}
  const code = query.from === 'stock_share' && stockCode(query.ts_code)
  return code ? { ts_code: code, symbol: code.split('.')[0], name: '正在加载', fromShare: true } : null
}
module.exports = { STOCK_PAGE, stockCode, shareEntry, parseShareEntry }
