// 展示格式化函数：接口原始值不直接拼接到页面。
function percent(value) {
  if (value === null || value === undefined || value === '') return '--'
  const number = Number(value)
  if (Number.isNaN(number)) return '--'
  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`
}

function dataState(updatedAt, stale) {
  if (stale) return '数据过期'
  return updatedAt ? `更新于 ${updatedAt}` : '暂无更新时间'
}

module.exports = { percent, dataState }
