// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, refreshPredictions } = require('../../utils/api')

function normalizeItems(items = [], scope = 'today') {
  const ruleLabels = {
    severe_30d: '30日严重异动',
    severe_10d: '10日严重异动',
    ordinary_3d: '普通异动',
    same_direction_10d: '同向次数',
  }
  return items.map((item, index) => {
    const rawChange = item.change == null || item.change === '' ? '' : String(item.change)
    const change = /^[-+]?0\.00%$/.test(rawChange) && !item.changeSource ? '--' : (rawChange || '--')
    const triggered = Boolean(item.triggered || item.cardTone === 'triggered')
    const deviationLabel = item.deviationLabel || item.deviation || '--'
    const triggerValue = item.triggerValue || '--'
    const window = item.predictionWindow || item.deviationWindow || ''
    return { ...item, deviationLabel, triggerValue, ruleLabel: item.ruleLabel || ruleLabels[item.ruleKey] || item.rule || '异动预测', triggered, showCurrentChange: scope !== 'next_day', alertText: triggered ? (item.alertText || '已触发') : '', cardTone: triggered ? 'triggered' : '', predictionKey: item.predictionKey || `${item.ts_code || index}-${item.ruleKey || window || index}`, change, changeClass: change === '--' ? 'neutral' : (change[0] === '-' ? 'down' : 'up') }
  })
}

function preserveItemOrder(currentItems, nextItems) {
  const nextByKey = new Map(nextItems.map(item => [item.predictionKey, item]))
  const ordered = currentItems.map(item => nextByKey.get(item.predictionKey)).filter(Boolean)
  const existingKeys = new Set(currentItems.map(item => item.predictionKey))
  return ordered.concat(nextItems.filter(item => !existingKeys.has(item.predictionKey)))
}

Page({
  data: { scope: 'today', items: [], refreshing: false, buttonRefreshing: false, loading: false, error: '', quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, nextDayAvailable: true, nextDayReason: '' },
  onLoad() { this.loadData() },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar()
    if (tabBar) tabBar.setData({ selected: 2 })
  },
  switchScope(event) {
    const scope = event.currentTarget.dataset.scope
    if (scope === 'next_day' && !this.data.nextDayAvailable) {
      wx.showToast({ title: this.data.nextDayReason || '收盘数据准备后开放', icon: 'none' })
      return
    }
    if (scope === this.data.scope) return
    this.setData({ scope, items: [], error: '' }, () => this.loadData())
  },
  loadData() {
    if (this.data.loading) return Promise.resolve()
    this.setData({ loading: true, error: '' })
    return getPredictions(this.data.scope).then((result) => {
      this.setData({ items: normalizeItems(result.items, this.data.scope), quoteUpdatedAt: result.quoteUpdatedAt || '', dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), loading: false, refreshing: false, error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
    }).catch(() => {
      this.setData({ loading: false, error: '预测数据暂时不可用，请稍后重试' })
    })
  },
  refresh(options = {}) {
    const showButtonLoading = options.showButtonLoading !== false
    if (this.data.buttonRefreshing) return Promise.resolve()
    this.setData({ buttonRefreshing: showButtonLoading, error: '' })
    return refreshPredictions(this.data.scope).then((result) => {
      const items = preserveItemOrder(this.data.items, normalizeItems(result.items, this.data.scope))
      this.setData({ buttonRefreshing: false, refreshing: false, items, quoteUpdatedAt: result.quoteUpdatedAt || '', dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
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
})
