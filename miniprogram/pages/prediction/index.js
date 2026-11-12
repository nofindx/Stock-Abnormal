// 异动预测页面：只请求后端批量快照，不在前端逐票扫描或模拟刷新。
const { getPredictions, getNextPredictionStatus, refreshPredictions } = require('../../utils/api')
const { createRewardedAccess } = require('../../utils/rewarded-access')
const { client: adsConfig } = require('../../utils/ads-config')
const { businessBoundaryKey: predictionBoundaryKey, startBoundaryWatch, stopBoundaryWatch } = require('../../utils/business-boundary')

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

function refreshFailureMessage(result = {}) {
  if (result.refresh_state === 'failed' || result.refreshStatus === 'failed') {
    return result.result_state === 'stale'
      ? '实时行情刷新失败，当前保留上次数据（可能已过期），请手动重试'
      : '实时行情刷新失败，请手动重试'
  }
  if (result.result_state === 'stale' || result.stale === true) return '当前显示上次数据（可能已过期），请手动刷新'
  return result.error || ''
}

function preserveItemOrder(currentItems, nextItems) {
  const nextByKey = new Map(nextItems.map(item => [item.predictionKey, item]))
  const ordered = currentItems.map(item => nextByKey.get(item.predictionKey)).filter(Boolean)
  const existingKeys = new Set(currentItems.map(item => item.predictionKey))
  return ordered.concat(nextItems.filter(item => !existingKeys.has(item.predictionKey)))
}

Page({
  data: { scope: 'today', items: [], refreshing: false, loading: false, error: '', quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, nextDayAvailable: true, nextDayReason: '', nextDayNoticeVisible: false, nextRewarded: false, nextUnlocked: false, nextUnlocking: false },
  onLoad() {
    this._predictionDisposed = false
    this._nextAccess = createRewardedAccess()
    this._scopeViews = {}
    adsConfig.policy('prediction_next').then(policy => { if (!this._predictionDisposed) this.setData({ nextRewarded: policy.mode === 'rewarded' }) })
    // A tab page lives across onShow calls. Initialize once per page instance;
    // subsequent quote updates are explicit pull-down refreshes.
    this._predictionBoundaryKey = predictionBoundaryKey()
    const initialBoundary = this._predictionBoundaryKey
    this.initialLoad = this.loadData().then((result) => {
      if (result && initialBoundary === this._predictionBoundaryKey && initialBoundary === predictionBoundaryKey() && result.realtimeRefreshAllowed && this.data.scope === 'today') return this.refresh()
      return result
    })
    return this.initialLoad
  },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar()
    if (tabBar) tabBar.setData({ selected: 2 })
    startBoundaryWatch(this, this.checkTimeBoundary)
  },
  checkTimeBoundary() {
    const boundary = predictionBoundaryKey()
    if (this._predictionBoundaryKey && boundary !== this._predictionBoundaryKey) {
      this._predictionBoundaryKey = boundary
      this._scopeViews = {}
      // Business-boundary changes replace S1 only. S2 remains first-open/manual.
      this.readResult = null
      this.setData({ items: [], quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, refreshing: false })
      return this.loadData()
    }
  },
  switchScope(event) {
    const scope = event.currentTarget.dataset.scope
    if (!['today', 'next_day'].includes(scope)) return
    if (this._predictionBoundaryKey && this._predictionBoundaryKey !== predictionBoundaryKey()) {
      return Promise.resolve(this.checkTimeBoundary()).then(() => this.switchScope(event))
    }
    if (scope === 'today') this._nextIntent = (this._nextIntent || 0) + 1
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
    if (scope === 'next_day') return this.unlockNextPrediction()
    return this.enterPredictionScope(scope)
  },
  cachedScope(scope) {
    const cached = (this._scopeViews || {})[scope]
    return cached && cached.boundary === predictionBoundaryKey() ? cached : null
  },
  saveScopeView(scope) {
    if (!this._scopeViews) this._scopeViews = {}
    if (!this.readResult || this.data.dataUnavailable) { delete this._scopeViews[scope]; return }
    this._scopeViews[scope] = { boundary: predictionBoundaryKey(), result: this.readResult,
      view: { items: this.data.items, quoteUpdatedAt: this.data.quoteUpdatedAt,
        dataQuality: this.data.dataQuality, dataUnavailable: this.data.dataUnavailable, error: this.data.error } }
  },
  enterPredictionScope(scope) {
    // Invalidate pending S1/S2 even when restoring a cached view without a read.
    this.readRequestId = (this.readRequestId || 0) + 1
    const cached = this.cachedScope(scope)
    if (cached) {
      this.readResult = cached.result
      this.setData({ ...cached.view, scope, loading: false, refreshing: false })
      getApp().globalData.lastPredictionRefresh = cached.result.quoteUpdatedAt || null
      return Promise.resolve(cached.result)
    }
    this.readResult = null
    this.setData({ scope, items: [], quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, refreshing: false, error: '' })
    return this.loadData()
  },
  async unlockNextPrediction() {
    if (this.data.nextUnlocking) return
    const intent = this._nextIntent = (this._nextIntent || 0) + 1
    const boundary = predictionBoundaryKey()
    const current = () => !this._predictionDisposed && intent === this._nextIntent && boundary === predictionBoundaryKey()
    if (!this._nextAccess) this._nextAccess = createRewardedAccess()
    this.setData({ nextUnlocking: true })
    try {
      const policy = await adsConfig.policy('prediction_next')
      if (!current()) return
      this.setData({ nextRewarded: policy.mode === 'rewarded' })
      // Free configuration preserves the existing API path and legacy compatibility.
      if (policy.mode === 'free') return this.enterPredictionScope('next_day')
      const cached = this.cachedScope('next_day')
      if (cached && this._nextAccess.has('prediction_next', cached.result.targetTradeDate)) {
        this._nextTarget = cached.result.targetTradeDate
        this.setData({ nextUnlocked: true })
        return this.enterPredictionScope('next_day')
      }
      const status = await getNextPredictionStatus()
      if (!current()) return
      if (!status.available) {
        if (this._scopeViews) delete this._scopeViews.next_day
        this._nextAccess.reset()
        this.setData({ nextUnlocked: false })
        wx.showToast({ title: status.reason || '次日预测数据暂不可用', icon: 'none' }); return
      }
      const target = status.targetTradeDate
      if (this._nextTarget && this._nextTarget !== target) {
        this._nextAccess.reset()
        this.setData({ nextUnlocked: false })
      }
      this._nextTarget = target
      const allowed = await this._nextAccess.request('prediction_next', target, current)
      if (!allowed || !current()) return
      const latest = await getNextPredictionStatus()
      if (!current()) return
      if (!latest.available || latest.targetTradeDate !== target) {
        if (this._scopeViews) delete this._scopeViews.next_day
        this._nextAccess.reset()
        this.setData({ nextUnlocked: false })
        wx.showToast({ title: '预测日期或状态已更新，请重新进入', icon: 'none' }); return
      }
      this.setData({ nextUnlocked: true })
      return this.enterPredictionScope('next_day')
    } catch (_) {
      if (current()) wx.showToast({ title: '次日状态读取失败，请稍后重试', icon: 'none' })
    } finally {
      if (!this._predictionDisposed) this.setData({ nextUnlocking: false })
    }
  },
  returnToTodayIfClosed(result, scope) {
    if (scope !== 'next_day' || result.nextDayAvailable !== false) return false
    this.readResult = null
    if (this._scopeViews) delete this._scopeViews.next_day
    if (this._nextAccess) this._nextAccess.reset()
    this.setData({ nextUnlocked: false })
    this.setData({ scope: 'today', items: [], quoteUpdatedAt: '', dataQuality: {}, dataUnavailable: false, refreshing: false, nextDayAvailable: false, nextDayReason: result.nextDayReason || '' })
    return true
  },
  loadData() {
    const scope = this.data.scope
    const boundary = predictionBoundaryKey()
    const requestId = this.readRequestId = (this.readRequestId || 0) + 1
    this.setData({ loading: true, error: '' })
    return getPredictions(scope).then((result) => {
      if (requestId !== this.readRequestId || scope !== this.data.scope || boundary !== predictionBoundaryKey()) return null
      if (result.nextDayAvailable === false && this._scopeViews) delete this._scopeViews.next_day
      if (this.returnToTodayIfClosed(result, scope)) return this.loadData()
      if (scope === 'next_day' && this.data.nextRewarded && result.targetTradeDate !== this._nextTarget) {
        if (this._nextAccess) this._nextAccess.reset()
        this.setData({ scope: 'today', items: [], loading: false, nextUnlocked: false })
        return this.loadData()
      }
      this.readResult = result
      const quoteUpdatedAt = formatUpdatedAt(result.quoteUpdatedAt)
      this.setData({ items: normalizeItems(result.items, this.data.scope), quoteUpdatedAt, dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), loading: false, refreshing: false, error: refreshFailureMessage(result), nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
      this.saveScopeView(scope)
      return result
    }).catch(() => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return
      this.setData({ loading: false, error: '预测数据暂时不可用，请稍后重试' })
    })
  },
  refresh() {
    if (this._predictionBoundaryKey && this._predictionBoundaryKey !== predictionBoundaryKey()) {
      return Promise.resolve(this.checkTimeBoundary()).then(() => this.refresh())
    }
    if (this.data.refreshing || this.data.loading) return Promise.resolve()
    this.setData({ refreshing: true, error: '' })
    const scope = this.data.scope
    const boundary = predictionBoundaryKey()
    const requestId = this.readRequestId
    return refreshPredictions(scope, this.readResult).then((result) => {
      if (requestId !== this.readRequestId || scope !== this.data.scope || boundary !== predictionBoundaryKey()) return null
      if (result.nextDayAvailable === false && this._scopeViews) delete this._scopeViews.next_day
      if (this.returnToTodayIfClosed(result, scope)) return this.loadData()
      if (scope === 'next_day' && this.data.nextRewarded && result.targetTradeDate !== this._nextTarget) {
        if (this._nextAccess) this._nextAccess.reset()
        this.setData({ scope: 'today', items: [], refreshing: false, nextUnlocked: false })
        return this.loadData()
      }
      this.readResult = result
      const items = preserveItemOrder(this.data.items, normalizeItems(result.items, this.data.scope))
      const quoteUpdatedAt = formatUpdatedAt(result.quoteUpdatedAt)
      this.setData({ refreshing: false, items, quoteUpdatedAt, dataQuality: result.dataQuality || {}, dataUnavailable: Boolean(result.dataUnavailable), error: refreshFailureMessage(result), nextDayAvailable: result.nextDayAvailable !== false, nextDayReason: result.nextDayReason || '' })
      getApp().globalData.lastPredictionRefresh = result.quoteUpdatedAt || null
      this.saveScopeView(scope)
      return result
    }).catch(() => {
      if (requestId !== this.readRequestId || scope !== this.data.scope) return null
      this.setData({ refreshing: false, error: '刷新失败，请稍后重试' })
      this.saveScopeView(scope)
      return { refreshStatus: 'failed' }
    })
  },
  onPullDownRefresh() {
    // 手动刷新取最新行情；已加载标签切回只恢复本页内存视图。
    return this.refresh().finally(() => wx.stopPullDownRefresh())
  },
  openStock(event) {
    const stock = this.data.items.find(item => item.ts_code === event.currentTarget.dataset.code)
    if (!stock) return
    getApp().globalData.pendingStock = stock
    wx.switchTab({ url: '/pages/stock/index' })
  },
  onHide() { stopBoundaryWatch(this) },
  onUnload() {
    this._predictionDisposed = true
    this._scopeViews = {}
    this._nextIntent = (this._nextIntent || 0) + 1
    if (this._nextAccess) this._nextAccess.dispose()
    stopBoundaryWatch(this)
    this.readRequestId = (this.readRequestId || 0) + 1
    if (this.nextDayNoticeTimer) clearTimeout(this.nextDayNoticeTimer)
  },
})

module.exports = { isIntradayRefreshResult, normalizeItems, predictionBoundaryKey, refreshFailureMessage }
