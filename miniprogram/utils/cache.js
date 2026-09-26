// 轻量本地缓存：只缓存最近结果和用户输入，不缓存 Token 或全市场行情。
const PREFIX = 'yidong:'

function set(key, value, ttl) {
  wx.setStorageSync(PREFIX + key, { value, expiresAt: Date.now() + ttl })
}

function get(key) {
  const item = wx.getStorageSync(PREFIX + key)
  if (!item || !item.expiresAt || item.expiresAt < Date.now()) return null
  return item.value
}

function remove(key) { wx.removeStorageSync(PREFIX + key) }

module.exports = { set, get, remove }
