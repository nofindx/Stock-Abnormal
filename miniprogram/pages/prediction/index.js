// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, refreshPredictions } = require('../../utils/api')

Page({
  data: { scope: 'today', items: [], refreshing: false, loading: false, error: '', updatedAt: '' },
  onLoad() { this.loadData() },
  openRules() {
    if (this.rulesNavigating) return
    this.rulesNavigating = true
    wx.navigateTo({
      url: '/pages/rules/index',
      animationType: 'none',
      animationDuration: 0,
      complete: () => { this.rulesNavigating = false }
    })
  },
  switchScope(event) {
    const scope = event.currentTarget.dataset.scope
    if (scope === this.data.scope) return
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({ scope, items: [], error: '' }, () => this.loadData())
  },
  loadData() {
    if (this.data.loading || this.data.refreshing) return Promise.resolve()
    this.setData({ loading: true, error: '' })
    return getPredictions(this.data.scope).then((result) => {
      const refreshing = Boolean(result.refreshing)
      this.setData({ items: result.items || [], updatedAt: result.updatedAt || '', loading: false, refreshing, error: result.error || '' })
      getApp().globalData.lastPredictionRefresh = result.updatedAt || null
      if (refreshing) this.schedulePoll()
    }).catch(() => {
      this.setData({ loading: false, error: '预测数据暂时不可用，请稍后重试' })
    })
  },
  schedulePoll() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = (this.pollAttempts || 0) + 1
    if (this.pollAttempts > 25) {
      this.setData({ refreshing: false })
      return
    }
    this.pollTimer = setTimeout(() => {
      getPredictions(this.data.scope).then((result) => {
        const refreshing = Boolean(result.refreshing)
        this.setData({ items: result.items || [], updatedAt: result.updatedAt || '', refreshing, error: result.error || '' })
        if (refreshing) this.schedulePoll()
        else this.pollAttempts = 0
      }).catch(() => this.schedulePoll())
    }, 1200)
  },
  refresh() {
    if (this.data.refreshing) return
    this.pollAttempts = 0
    this.setData({ refreshing: true, error: '' })
    refreshPredictions(this.data.scope).then((result) => {
      const refreshing = Boolean(result.refreshing)
      this.setData({ refreshing, items: result.items || [], updatedAt: result.updatedAt || '', error: result.error || '' })
      getApp().globalData.lastPredictionRefresh = result.updatedAt || null
      if (refreshing) this.schedulePoll()
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
  },
  onUnload() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
  }
})
