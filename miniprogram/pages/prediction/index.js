// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, refreshPredictions } = require('../../utils/api')

Page({
  data: { scope: 'today', items: [], refreshing: false, loading: false, error: '', updatedAt: '' },
  onLoad() { this.loadData() },
  switchScope(event) {
    const scope = event.currentTarget.dataset.scope
    if (scope === this.data.scope) return
    this.setData({ scope, items: [], error: '' }, () => this.loadData())
  },
  loadData() {
    if (this.data.loading || this.data.refreshing) return Promise.resolve()
    this.setData({ loading: true, error: '' })
    return getPredictions(this.data.scope).then((result) => {
      this.setData({ items: result.items || [], updatedAt: result.updatedAt || '', loading: false, error: '' })
      getApp().globalData.lastPredictionRefresh = result.updatedAt || null
    }).catch(() => {
      this.setData({ loading: false, error: '预测数据暂时不可用，请稍后重试' })
    })
  },
  refresh() {
    if (this.data.refreshing) return
    this.setData({ refreshing: true, error: '' })
    refreshPredictions(this.data.scope).then((result) => {
      this.setData({ refreshing: false, items: result.items || [], updatedAt: result.updatedAt || '' })
      getApp().globalData.lastPredictionRefresh = result.updatedAt || null
      wx.vibrateShort({ type: 'light' })
    }).catch(() => {
      this.setData({ refreshing: false, error: '刷新失败，请稍后重试' })
    })
  },
  openStock(event) {
    const stock = this.data.items.find(item => item.ts_code === event.currentTarget.dataset.code)
    if (!stock) return
    getApp().globalData.pendingStock = stock
    wx.switchTab({ url: '/pages/stock/index' })
  }
})
