// 监控池页面：监控期、风险类型和剩余交易日全部使用后端结果。
const { getMonitor } = require('../../utils/api')

Page({
  data: { tab: 'current', type: 'all', hideST: false, items: [], rawItems: [], updatedAt: '', loading: false, refreshing: false, waitingSeconds: 0, error: '', skeletonRows: [0, 1, 2, 3], types: [{ key: 'all', label: '全部' }, { key: 'risk', label: '风险提示' }, { key: 'severe', label: '严重异动' }] },
  onLoad() { this.restoreCache(); this.loadData() },
  onPullDownRefresh() { this.loadData().finally(() => wx.stopPullDownRefresh()) },
  selectTab(event) {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({ tab: event.currentTarget.dataset.tab }, () => { if (!this.restoreCache()) this.setData({ items: [], rawItems: [], updatedAt: '' }); this.loadData() })
  },
  selectType(event) {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({ type: event.currentTarget.dataset.type }, () => { if (!this.restoreCache()) this.setData({ items: [], rawItems: [], updatedAt: '' }); this.loadData() })
  },
  toggleST(event) { this.setData({ hideST: event.detail.value }, () => this.applyFilters(this.data.rawItems || [])) },
  cacheKey() { return `monitorSnapshot:${this.data.tab}:${this.data.type}` },
  restoreCache() {
    try {
      const cached = wx.getStorageSync(this.cacheKey())
      if (!cached || !Array.isArray(cached.items)) return false
      this.setData({ rawItems: cached.items, updatedAt: cached.updatedAt || '', error: '' })
      this.applyFilters(cached.items)
      return true
    } catch (error) {
      // 本地缓存不可用时继续请求后端，不影响首次打开。
      return false
    }
  },
  saveCache(result) {
    try { wx.setStorageSync(this.cacheKey(), { items: result.items || [], updatedAt: result.updatedAt || '' }) } catch (error) {}
  },
  loadData() {
    if (this.data.loading) return Promise.resolve()
    this.waitStartedAt = Date.now()
    if (this.waitTimer) clearInterval(this.waitTimer)
    this.waitTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - this.waitStartedAt) / 1000)
      this.setData({ waitingSeconds: seconds })
    }, 1000)
    this.setData({ loading: true, error: '' })
    return getMonitor({ status: this.data.tab, type: this.data.type }).then((result) => {
      const items = result.items || []
      const waiting = Boolean(result.refreshing) && !items.length
      // 冷启动期间不覆盖本地成功快照，避免缓存内容被空响应清掉。
      if (!waiting) {
        this.saveCache(result)
        this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
        this.applyFilters(items)
      }
      this.setData({ loading: waiting && !(this.data.rawItems || []).length, waitingSeconds: waiting ? this.data.waitingSeconds : 0, error: result.error || '', refreshing: Boolean(result.refreshing) })
      if (!waiting && this.waitTimer) { clearInterval(this.waitTimer); this.waitTimer = null }
      if (result.refreshing) this.schedulePoll()
    }).catch(() => {
      if (this.waitTimer) { clearInterval(this.waitTimer); this.waitTimer = null }
      this.setData({ loading: false, waitingSeconds: 0, error: '监控数据暂时不可用，请稍后重试' })
    })
  },
  schedulePoll() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = (this.pollAttempts || 0) + 1
    if (this.pollAttempts > 15) {
      if (this.waitTimer) { clearInterval(this.waitTimer); this.waitTimer = null }
      this.setData({ loading: false, refreshing: false, waitingSeconds: 0, error: this.data.items.length ? '今日快照更新超时，当前仍显示上次成功数据' : '今日监控快照获取超时，请稍后重试' })
      return
    }
    this.pollTimer = setTimeout(() => {
      getMonitor({ status: this.data.tab, type: this.data.type }).then((result) => {
        const items = result.items || []
        const waiting = Boolean(result.refreshing) && !items.length
        if (!waiting) {
          this.saveCache(result)
          this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
          this.applyFilters(items)
        }
        this.setData({ loading: waiting && !(this.data.rawItems || []).length, waitingSeconds: waiting ? this.data.waitingSeconds : 0, refreshing: Boolean(result.refreshing), error: result.error || '' })
        if (!waiting && this.waitTimer) { clearInterval(this.waitTimer); this.waitTimer = null }
        if (result.refreshing) this.schedulePoll()
        else this.pollAttempts = 0
      }).catch(() => this.schedulePoll())
    }, 1200)
  },
  applyFilters(items) {
    let filtered = items.map(item => ({
      ...item,
      riskTone: item.riskTone || (item.monitorType === '30日严重异动' ? 'severe-30d' : item.monitorType === '10日严重异动' ? 'severe-10d' : 'ordinary')
    }))
    if (this.data.hideST) filtered = filtered.filter(item => !item.isST && !String(item.name || '').toUpperCase().includes('ST'))
    filtered.sort((left, right) => Number(left.days == null ? 9999 : left.days) - Number(right.days == null ? 9999 : right.days))
    this.setData({ items: filtered })
  },
  onUnload() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    if (this.waitTimer) clearInterval(this.waitTimer)
  }
})
