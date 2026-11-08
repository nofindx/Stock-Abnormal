// 重点监控页面：监控期、风险类型和剩余天数全部使用后端结果。
const { getMonitor } = require('../../utils/api')
const { openPdf } = require('../../utils/open-pdf')
const { businessDateKey, startBoundaryWatch, stopBoundaryWatch } = require('../../utils/business-boundary')

Page({
  data: { type: 'all', items: [], rawItems: [], visibleCount: 0, updatedAt: '', loading: false, refreshing: false, error: '', skeletonRows: [0, 1, 2, 3], types: [{ key: 'all', label: '全部' }, { key: 'risk', label: '风险提示' }, { key: 'severe', label: '严重异动' }, { key: 'delisting', label: '退市整理' }] },
  onLoad() { this._monitorBusinessDate = businessDateKey(); this.loadData() },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar()
    if (tabBar) tabBar.setData({ selected: 1 })
    this._monitorHidden = false
    startBoundaryWatch(this, this.checkTimeBoundary, true)
    if (this.data.refreshing && !this.pollTimer) this.schedulePoll()
  },
  checkTimeBoundary() {
    const date = businessDateKey()
    if (this._monitorBusinessDate && date !== this._monitorBusinessDate) {
      this._monitorBusinessDate = date
      if (this.pollTimer) clearTimeout(this.pollTimer)
      this.pollTimer = null
      this.pollAttempts = 0
      this.setData({ items: [], rawItems: [], visibleCount: 0, updatedAt: '', error: '', loading: true, refreshing: false })
      return this.loadData()
    }
  },
  onPullDownRefresh() { this.loadData().finally(() => wx.stopPullDownRefresh()) },
  refreshNow() {
    if (this.data.loading || this.data.refreshing) return
    this.loadData()
  },
  selectType(event) {
    this.requestSeq = (this.requestSeq || 0) + 1
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({
      type: event.currentTarget.dataset.type,
      items: [], rawItems: [], visibleCount: 0, updatedAt: '', error: '', loading: true
    }, () => this.loadData())
  },
  loadData() {
    const requestType = this.data.type
    const requestId = (this.requestSeq || 0) + 1
    this.requestSeq = requestId
    this.waitStartedAt = Date.now()
    this.setData({ loading: true, error: '' })
    const request = getMonitor({ status: 'current', type: requestType }).then((result) => {
      // 筛选切换或新请求已经发生时，丢弃晚返回的旧响应，不能覆盖当前列表。
      if (requestId !== this.requestSeq || requestType !== this.data.type) return
      const items = result.items || []
      const waiting = Boolean(result.refreshing) && !items.length
      if (!waiting) {
        this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
        this.applyFilters(items)
      }
      this.setData({ loading: waiting && !(this.data.rawItems || []).length, error: result.error || '', refreshing: Boolean(result.refreshing) })
      if (result.refreshing) this.schedulePoll()
    }).catch(() => {
      if (requestId === this.requestSeq && requestType === this.data.type) this.setData({ loading: false, error: '监控数据暂时不可用，请稍后重试' })
    })
    return request
  },
  schedulePoll() {
    if (this._monitorHidden) return
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = (this.pollAttempts || 0) + 1
    if (this.pollAttempts > 15) {
      this.setData({ loading: false, refreshing: false, error: this.data.items.length ? '今日快照更新超时，当前仍显示上次成功数据' : '今日监控快照获取超时，请稍后重试' })
      return
    }
    const pollType = this.data.type
    const pollRequestId = this.requestSeq
    this.pollTimer = setTimeout(() => {
      this.pollTimer = null
      getMonitor({ status: 'current', type: pollType }).then((result) => {
        if (pollRequestId !== this.requestSeq || pollType !== this.data.type) return
        const items = result.items || []
        const waiting = Boolean(result.refreshing) && !items.length
        if (!waiting) {
          this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
          this.applyFilters(items)
        }
        this.setData({ loading: waiting && !(this.data.rawItems || []).length, refreshing: Boolean(result.refreshing), error: result.error || '' })
        if (result.refreshing) this.schedulePoll()
        else this.pollAttempts = 0
      }).catch(() => {
        if (pollRequestId === this.requestSeq && pollType === this.data.type) this.schedulePoll()
      })
    }, 1200)
  },
  applyFilters(items) {
    let filtered = items.filter(isVisibleMonitorItem).map(item => ({
      ...item,
      // 剩余自然日由后端计算，前端只负责格式化展示。
      days: formatRemainingDays(item.days),
      riskTone: item.riskTone || 'ordinary'
    }))
    filtered.sort((left, right) => Number(left.days == null ? 9999 : left.days) - Number(right.days == null ? 9999 : right.days))
    this.setData({ items: filtered, visibleCount: filtered.length })
  },
  openStock(event) {
    const tsCode = event.currentTarget.dataset.code
    if (!tsCode) return
    const stock = this.data.items.find(item => item.ts_code === tsCode)
    getApp().globalData.pendingStock = stock || { ts_code: tsCode, symbol: tsCode.split('.')[0], name: '' }
    wx.switchTab({ url: '/pages/stock/index' })
  },
  openSource(event) {
    const url = event.currentTarget.dataset.url
    if (!url) return
    if (this.openingSource) return
    this.openingSource = true
    openPdf(url, {
      onError: () => wx.showToast({ title: '公告暂时无法打开，请稍后重试', icon: 'none' })
    }).catch(() => {}).finally(() => { this.openingSource = false })
  },
  onHide() {
    this._monitorHidden = true
    stopBoundaryWatch(this)
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollTimer = null
  },
  onUnload() {
    this.onHide()
    this.requestSeq = (this.requestSeq || 0) + 1
    if (this.pollTimer) clearTimeout(this.pollTimer)
  }
})

function formatRemainingDays(value) {
  if (value == null || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value)
  return String(Math.max(0, Math.trunc(number))).padStart(2, '0')
}

function isVisibleMonitorItem(item) {
  // 退市整理单独保留；风险提示与严重异动按产品去杂规则只影响页面展示。
  if (item.riskTone === 'delisting-warning') return true
  if (!['ordinary', 'severe-10d', 'severe-30d'].includes(item.riskTone)) return true
  if (['severe-10d', 'severe-30d'].includes(item.riskTone) && item.eventDirection === 'down') return false
  const code = String(item.symbol || item.ts_code || '').match(/\d{6}/)
  const name = String(item.name || '').replace(/\s+/g, '').toUpperCase()
  return !(code && /^[15]/.test(code[0])) && !/^\*?ST/.test(name) && !name.includes('退')
}

if (typeof module !== 'undefined') module.exports = { isVisibleMonitorItem, businessDateKey }
