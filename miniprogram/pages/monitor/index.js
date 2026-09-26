// 监控池页面：监控期、风险类型和剩余交易日全部使用后端结果。
const { getMonitor } = require('../../utils/api')

Page({
  data: { tab: 'current', type: 'all', hideST: false, items: [], updatedAt: '', loading: false, error: '', types: [{ key: 'all', label: '全部' }, { key: 'risk', label: '风险提示' }, { key: 'severe', label: '严重异动' }] },
  onLoad() { this.loadData() },
  onPullDownRefresh() { this.loadData().finally(() => wx.stopPullDownRefresh()) },
  selectTab(event) { this.setData({ tab: event.currentTarget.dataset.tab }, () => this.loadData()) },
  selectType(event) { this.setData({ type: event.currentTarget.dataset.type }, () => this.loadData()) },
  toggleST(event) { this.setData({ hideST: event.detail.value }, () => this.applyFilters(this.data.rawItems || [])) },
  loadData() {
    if (this.data.loading) return Promise.resolve()
    this.setData({ loading: true, error: '' })
    return getMonitor({ status: this.data.tab, type: this.data.type }).then((result) => {
      const items = result.items || []
      this.setData({ rawItems: items, updatedAt: result.updatedAt || '', loading: false, error: '' })
      this.applyFilters(items)
    }).catch(() => {
      this.setData({ loading: false, error: '监控数据暂时不可用，请稍后重试' })
    })
  },
  applyFilters(items) {
    let filtered = items.slice()
    if (this.data.hideST) filtered = filtered.filter(item => !item.isST && !String(item.name || '').toUpperCase().includes('ST'))
    filtered.sort((left, right) => Number(left.days == null ? 9999 : left.days) - Number(right.days == null ? 9999 : right.days))
    this.setData({ items: filtered })
  }
})
