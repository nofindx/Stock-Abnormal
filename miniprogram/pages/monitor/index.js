// 监控池页面：监控期、风险类型和剩余自然日全部使用后端结果。
const { getMonitor } = require('../../utils/api')

Page({
  data: { type: 'all', hideST: false, items: [], rawItems: [], visibleCount: 0, updatedAt: '', loading: false, refreshing: false, error: '', skeletonRows: [0, 1, 2, 3], types: [{ key: 'all', label: '全部' }, { key: 'risk', label: '风险提示' }, { key: 'severe', label: '严重异动' }] },
  onLoad() { this.restoreCache(); this.loadData() },
  onPullDownRefresh() { this.loadData().finally(() => wx.stopPullDownRefresh()) },
  selectType(event) {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = 0
    this.setData({ type: event.currentTarget.dataset.type }, () => { if (!this.restoreCache()) this.setData({ items: [], rawItems: [], updatedAt: '' }); this.loadData() })
  },
  toggleST(event) {
    const hasSwitchValue = event && event.detail && typeof event.detail.value !== 'undefined'
    const hideST = hasSwitchValue ? Boolean(event.detail.value) : !this.data.hideST
    this.setData({ hideST }, () => this.applyFilters(this.data.rawItems || []))
  },
  refreshSnapshot() {
    if (this.data.loading || this.data.refreshing) return
    this.pollAttempts = 0
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.loadData()
  },
  cacheKey() { return `monitorSnapshot:v3:current:${this.data.type}` },
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
    this.setData({ loading: true, error: '' })
    return getMonitor({ status: 'current', type: this.data.type }).then((result) => {
      const items = result.items || []
      const waiting = Boolean(result.refreshing) && !items.length
      // 冷启动期间不覆盖本地成功快照，避免缓存内容被空响应清掉。
      if (!waiting) {
        this.saveCache(result)
        this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
        this.applyFilters(items)
      }
      this.setData({ loading: waiting && !(this.data.rawItems || []).length, error: result.error || '', refreshing: Boolean(result.refreshing) })
      if (result.refreshing) this.schedulePoll()
    }).catch(() => {
      this.setData({ loading: false, error: '监控数据暂时不可用，请稍后重试' })
    })
  },
  schedulePoll() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollAttempts = (this.pollAttempts || 0) + 1
    if (this.pollAttempts > 15) {
      this.setData({ loading: false, refreshing: false, error: this.data.items.length ? '今日快照更新超时，当前仍显示上次成功数据' : '今日监控快照获取超时，请稍后重试' })
      return
    }
    this.pollTimer = setTimeout(() => {
      getMonitor({ status: 'current', type: this.data.type }).then((result) => {
        const items = result.items || []
        const waiting = Boolean(result.refreshing) && !items.length
        if (!waiting) {
          this.saveCache(result)
          this.setData({ rawItems: items, updatedAt: result.updatedAt || '' })
          this.applyFilters(items)
        }
        this.setData({ loading: waiting && !(this.data.rawItems || []).length, refreshing: Boolean(result.refreshing), error: result.error || '' })
        if (result.refreshing) this.schedulePoll()
        else this.pollAttempts = 0
      }).catch(() => this.schedulePoll())
    }, 1200)
  },
  applyFilters(items) {
    let filtered = items.map(item => ({
      ...item,
      // 501/513/920 等非重点标的仍展示，但降低名称、代码和日期信息的视觉权重。
      mutedSecurity: /^(1|5|9)/.test(String(item.symbol || '')),
      riskTone: item.riskTone || (item.monitorType === '30日严重异动' ? 'severe-30d' : item.monitorType === '10日严重异动' ? 'severe-10d' : 'ordinary')
    }))
    if (this.data.hideST) filtered = filtered.filter(item => !item.isST && !String(item.name || '').toUpperCase().includes('ST'))
    filtered.sort((left, right) => Number(left.days == null ? 9999 : left.days) - Number(right.days == null ? 9999 : right.days))
    this.setData({ items: filtered, visibleCount: filtered.length })
  },
  openSource(event) {
    const url = event.currentTarget.dataset.url
    if (!url) return
    // 只交给系统浏览器或复制链接，不在小程序内下载、缓存公告文件。
    if (typeof wx.openUrl === 'function') {
      wx.openUrl({
        url,
        fail: () => this.copySourceLink(url)
      })
      return
    }
    this.copySourceLink(url)
  },
  copySourceLink(url) {
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '原文链接已复制', icon: 'none' }) })
  },
  onUnload() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
  }
})
