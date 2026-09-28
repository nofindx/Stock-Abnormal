// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, refreshPredictions } = require('../../utils/api')

function normalizeItems(items = []) {
  return items.map((item) => {
    const change = item.change == null || item.change === '' ? '--' : String(item.change)
    return { ...item, change, changeClass: change === '--' ? 'neutral' : (change[0] === '-' ? 'down' : 'up') }
  })
}

Page({
  data: { scope: 'today', items: [], refreshing: false, buttonRefreshing: false, loading: false, error: '', updatedAt: '', dataMessage: '', nextDayAvailable: true, nextDayReason: '' },
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
    if (scope === 'next_day' && !this.data.nextDayAvailable) {
      wx.showToast({ title: this.data.nextDayReason || '收盘数据准备后开放', icon: 'none' })
      return
    }
    if (scope === this.data.scope) return
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({ scope, items: [], error: '' }, () => this.loadData())
  },
  loadData() {
    if (this.data.loading) return Promise.resolve()
    this.setData({ loading: true, error: '' })
    return getPredictions(this.data.scope).then((result) => {
      const refreshing = Boolean(result.refreshing)
      this.setData({ items: normalizeItems(result.items), updatedAt: result.updatedAt || '', dataMessage: (result.dataQuality && result.dataQuality.message) || '', loading: false, refreshing, error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
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
        this.setData({ items: normalizeItems(result.items), updatedAt: result.updatedAt || '', dataMessage: (result.dataQuality && result.dataQuality.message) || '', refreshing, error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
        if (refreshing) this.schedulePoll()
        else this.pollAttempts = 0
      }).catch(() => this.schedulePoll())
    }, 1200)
  },
  refresh(options = {}) {
    const showButtonLoading = options.showButtonLoading !== false
    if (this.data.buttonRefreshing) return Promise.resolve()
    this.pollAttempts = 0
    this.setData({ buttonRefreshing: showButtonLoading, error: '' })
    return refreshPredictions(this.data.scope).then((result) => {
      const refreshing = Boolean(result.refreshing)
      this.setData({ buttonRefreshing: false, refreshing, items: normalizeItems(result.items), updatedAt: result.updatedAt || '', dataMessage: (result.dataQuality && result.dataQuality.message) || '', error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.updatedAt || null
      if (refreshing) this.schedulePoll()
      wx.vibrateShort({ type: 'light' })
    }).catch(() => {
      this.setData({ buttonRefreshing: false, refreshing: false, error: '刷新失败，请稍后重试' })
    })
  },
  onPullDownRefresh() {
    // 下拉刷新与页面刷新按钮使用同一条用户触发链路；切换 Tab 仍只读数据。
    return this.refresh({ showButtonLoading: false }).finally(() => wx.stopPullDownRefresh())
  },
  openStock(event) {
    const stock = this.data.items.find(item => item.ts_code === event.currentTarget.dataset.code)
    if (!stock) return
    getApp().globalData.pendingStock = stock
    wx.switchTab({ url: '/pages/stock/index' })
  },
  onUnload() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
  },
  openPrivacy() { wx.navigateTo({ url: '/pages/privacy/index', animationType: 'none' }) },
  openAgreement() { wx.navigateTo({ url: '/pages/agreement/index', animationType: 'none' }) }
})
