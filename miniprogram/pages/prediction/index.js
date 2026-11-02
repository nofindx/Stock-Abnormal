// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, refreshPredictions } = require('../../utils/api')

function isIntradayRefreshResult(result = {}) {
  const quality = result.dataQuality || {}
  return result.refreshStatus === 'applied' && (quality.intraday === true || result.dataStage === 'realtime')
}

function normalizeItems(items = [], scope = 'today') {
  const ruleLabels = {
    severe_30d: '30日异动',
    severe_10d: '10日异动',
    ordinary_3d: '普通异动',
    same_direction_10d: '10日同向',
  }
  return items.map((item, index) => {
    const rawChange = item.change == null || item.change === '' ? '' : String(item.change)
    const change = /^[-+]?0\.00%$/.test(rawChange) && !item.changeSource ? '--' : (rawChange || '--')
    const triggered = Boolean(item.triggered || item.cardTone === 'triggered')
    const deviationLabel = item.deviationLabel || item.deviation || '--'
    const triggerValue = item.triggerValue || '--'
    const window = item.predictionWindow || item.deviationWindow || ''
    const riskTone = item.ruleKey === 'ordinary_3d' ? 'ordinary' : 'severe'
    return { ...item, deviationLabel, triggerValue, ruleLabel: item.ruleLabel || ruleLabels[item.ruleKey] || item.rule || '异动预测', triggered, todayTriggered: scope === 'today' && triggered, riskTone, showCurrentChange: scope !== 'next_day', alertText: triggered ? (item.alertText || '已触发') : '', cardTone: triggered ? 'triggered' : '', predictionKey: item.predictionKey || `${item.ts_code || index}-${item.ruleKey || window || index}`, change, changeClass: change === '--' ? 'neutral' : (change[0] === '-' ? 'down' : 'up') }
  })
}

function formatUpdatedAt(value) {
  return String(value || '').replace('T', ' ')
}

function preserveItemOrder(currentItems, nextItems) {
  const nextByKey = new Map(nextItems.map(item => [item.predictionKey, item]))
  const ordered = currentItems.map(item => nextByKey.get(item.predictionKey)).filter(Boolean)
  const existingKeys = new Set(currentItems.map(item => item.predictionKey))
  return ordered.concat(nextItems.filter(item => !existingKeys.has(item.predictionKey)))
}

Page({
  data: { scope: 'today', items: [], refreshing: false, loading: false, error: '', quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, nextDayAvailable: true, nextDayReason: '', nextDayNoticeVisible: false },
  onLoad() {
    // A tab page lives across onShow calls. Initialize once per page instance;
    // subsequent quote updates are explicit pull-down refreshes.
    this.initialLoad = this.loadData().then((result) => {
      if (result && result.realtimeRefreshAllowed && this.data.scope === 'today') return this.refresh()
      return result
    })
    return this.initialLoad
  },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar()
    if (tabBar) tabBar.setData({ selected: 2 })
  },
  switchScope(event) {
    const scope = event.currentTarget.dataset.scope
    if (scope === 'next_day' && !this.data.nextDayAvailable) {
      const reason = this.data.nextDayReason || '收盘数据准备后开放'
      if (reason === '交易时间内暂不开放次日预测') {
        if (this.nextDayNoticeTimer) clearTimeout(this.nextDayNoticeTimer)
        this.setData({ nextDayNoticeVisible: true })
        this.nextDayNoticeTimer = setTimeout(() => {
          this.nextDayNoticeTimer = null
          this.setData({ nextDayNoticeVisible: false })
        }, 1800)
      } else {
        wx.showToast({ title: reason, icon: 'none' })
      }
      return
    }
    if (scope === this.data.scope) return
    this.setData({ scope, items: [], error: '' }, () => this.loadData())
  },
  loadData() {
    const scope = this.data.scope
    const requestId = this.readRequestId = (this.readRequestId || 0) + 1
    this.setData({ loading: true, error: '' })
    return getPredictions(scope).then((result) => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return null
      this.readResult = result
      const quoteUpdatedAt = formatUpdatedAt(result.quoteUpdatedAt)
      this.setData({ items: normalizeItems(result.items, this.data.scope), quoteUpdatedAt, dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), loading: false, refreshing: false, error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
      return result
    }).catch(() => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return
      this.setData({ loading: false, error: '预测数据暂时不可用，请稍后重试' })
    })
  },
  refresh() {
    if (this.data.refreshing || this.data.loading) return Promise.resolve()
    this.setData({ refreshing: true, error: '' })
    const scope = this.data.scope
    const requestId = this.readRequestId
    return refreshPredictions(scope, this.readResult).then((result) => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return null
      this.readResult = result
      const items = preserveItemOrder(this.data.items, normalizeItems(result.items, this.data.scope))
      const quoteUpdatedAt = formatUpdatedAt(result.quoteUpdatedAt)
      this.setData({ refreshing: false, items, quoteUpdatedAt, dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), error: result.error || '', nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
      return result
    }).catch(() => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return null
      this.setData({ refreshing: false, error: '刷新失败，请稍后重试' })
      return { refreshStatus: 'failed' }
    })
  },
  onPullDownRefresh() {
    // 仅使用小程序系统下拉刷新；切换 Tab 仍只读已发布数据集。
    return this.refresh().finally(() => wx.stopPullDownRefresh())
  },
  openStock(event) {
    const stock = this.data.items.find(item => item.ts_code === event.currentTarget.dataset.code)
    if (!stock) return
    getApp().globalData.pendingStock = stock
    wx.switchTab({ url: '/pages/stock/index' })
  },
  onUnload() {
    if (this.nextDayNoticeTimer) clearTimeout(this.nextDayNoticeTimer)
  },
})

module.exports = { isIntradayRefreshResult, normalizeItems }
