// 单股查询页：只负责搜索、展示和模拟输入，计算结果来自后端真实接口。
const { searchStocks, getStockDetail, getStockAnnouncements } = require('../../utils/api')

function createInputRows(days, dates = []) {
  // 未来交易日假设行：股票涨幅和指数涨幅由用户输入，其余列由计算结果填充。
  return Array.from({ length: Number(days) }, (_, index) => ({
    day: `T+${index + 1}`,
    date: dates[index] || '--',
    stock: '', index: '', deviation3: '--', deviation10: '--', deviation30: '--', space: '--', deviationClass: ''
  }))
}

function buildMatrixGroups(rows) {
  const fields = [
    { key: 'stock', label: '股票涨幅', editable: true },
    { key: 'index', label: '指数涨幅', editable: true },
    { key: 'deviation3', label: '3日偏离' },
    { key: 'deviation10', label: '10日偏离' },
    { key: 'deviation30', label: '30日偏离' },
    { key: 'space', label: '安全空间' }
  ]
  const groups = []
  for (let start = 0; start < rows.length; start += 5) {
    const columns = Array.from({ length: 5 }, (_, offset) => {
      const rowIndex = start + offset
      const item = rows[rowIndex]
      return { day: item ? item.day : `T+${rowIndex + 1}`, date: item ? item.date : '--', rowIndex, placeholder: !item }
    })
    groups.push({
      groupKey: start,
      columns,
      countClass: `matrix-count-${columns.length}`,
      rows: fields.map(field => ({
        key: field.key,
        label: field.label,
        editable: Boolean(field.editable),
        values: columns.map(column => ({
          value: rows[column.rowIndex] ? rows[column.rowIndex][field.key] : '',
          placeholder: column.placeholder,
          className: rows[column.rowIndex] ? rows[column.rowIndex].deviationClass : '',
          rowIndex: column.rowIndex,
          field: field.key,
          editable: Boolean(field.editable) && !column.placeholder
        }))
      }))
    })
  }
  return groups
}

function numberValue(value) {
  const parsed = Number.parseFloat(String(value).replace('%', ''))
  return Number.isFinite(parsed) ? parsed : 0
}

Page({
  data: {
    query: '', suggestions: [], suggestionHint: false, selected: null, detail: null,
    detailLoading: false, detailError: '', searchError: '', searchFocused: false,
    futureTradeDates: [], announcements: [], announcementsLoading: false, announcementsAvailable: true,
    dayOptions: ['2', '5', '10'], dayOptionIndex: 0,
    inputRows: createInputRows(2), matrixGroups: buildMatrixGroups(createInputRows(2))
  },
  onLoad(options) { if (options.ts_code) this.loadStockByCode(options.ts_code) },
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
  onShow() {
    const pending = getApp().globalData.pendingStock
    if (pending) { getApp().globalData.pendingStock = null; this.chooseStock(pending) }
  },
  onQueryInput(event) {
    const query = event.detail.value
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.searchRequestId = (this.searchRequestId || 0) + 1
    const requestId = this.searchRequestId
    this.setData({ query, suggestions: [], suggestionHint: false, searchError: '', selected: null, detail: null, detailError: '' })
    if (!query.trim()) return
    this.searchTimer = setTimeout(() => {
      searchStocks(query).then((result) => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: result.items || [], suggestionHint: Boolean(result.hasMore), searchError: '' })
      }).catch(() => {
        if (requestId !== this.searchRequestId) return
        this.setData({ suggestions: [], suggestionHint: false, searchError: '搜索失败，请稍后重试' })
      })
    }, 250)
  },
  onSearchFocus() { this.setData({ searchFocused: true }) },
  onSearchBlur() { this.setData({ searchFocused: false }) },
  clearQuery() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.searchRequestId = (this.searchRequestId || 0) + 1
    this.setData({ query: '', suggestions: [], suggestionHint: false, searchError: '', selected: null, detail: null, detailError: '' })
  },
  loadStockByCode(tsCode) {
    if (!tsCode) return
    this.chooseStock({ ts_code: tsCode, symbol: tsCode.split('.')[0], name: '正在加载' })
  },
  chooseStock(eventOrStock) {
    const stock = eventOrStock && eventOrStock.currentTarget
      ? this.data.suggestions[eventOrStock.currentTarget.dataset.index]
      : eventOrStock
    if (!stock || !stock.ts_code) return
    getApp().globalData.lastStock = stock
    const inputRows = createInputRows(this.data.dayOptions[this.data.dayOptionIndex], this.data.futureTradeDates)
    this.detailRequestId = (this.detailRequestId || 0) + 1
    const requestId = this.detailRequestId
    this.setData({ selected: stock, detail: null, detailLoading: true, detailError: '', query: stock.name === '正在加载' ? stock.ts_code : `${stock.name} ${stock.symbol}`, suggestions: [], inputRows, matrixGroups: buildMatrixGroups(inputRows) })
    getStockDetail(stock.ts_code).then((detail) => {
      if (requestId !== this.detailRequestId) return
      const futureTradeDates = detail.futureTradeDates || []
      const rows = createInputRows(this.data.dayOptions[this.data.dayOptionIndex], futureTradeDates)
      this.setData({ selected: { ...stock, ...detail }, detail, detailLoading: false, detailError: '', futureTradeDates, inputRows: rows, matrixGroups: buildMatrixGroups(rows), query: `${detail.name} ${detail.symbol}`, announcements: [], announcementsLoading: true, announcementsAvailable: true })
      return getStockAnnouncements(stock.ts_code).then((result) => {
        if (requestId !== this.detailRequestId) return
        this.setData({ announcements: result.items || [], announcementsLoading: false, announcementsAvailable: result.available !== false })
      })
    }).catch(() => {
      if (requestId !== this.detailRequestId) return
      this.setData({ detailLoading: false, announcementsLoading: false, detailError: '行情暂时不可用，请稍后重试' })
    })
  },
  changeDays(event) {
    const dayOptionIndex = Number(event.currentTarget ? event.currentTarget.dataset.index : event.detail.value)
    const inputRows = createInputRows(this.data.dayOptions[dayOptionIndex], this.data.futureTradeDates)
    this.setData({ dayOptionIndex, inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  editRow(event) {
    const { row, field } = event.currentTarget.dataset
    const value = event.detail.value
    const rows = this.data.inputRows.map((item, index) => {
      if (index !== Number(row)) return item
      const stock = field === 'stock' ? value : item.stock
      const indexValue = field === 'index' ? value : item.index
      const deviation = numberValue(stock) - numberValue(indexValue)
      const display = stock || indexValue ? `${deviation >= 0 ? '+' : ''}${deviation.toFixed(2)}%` : '--'
      return { ...item, [field]: value, deviation3: display, deviation10: display, deviation30: display, space: stock || indexValue ? `${Math.max(0, 100 - Math.abs(deviation)).toFixed(2)}%` : '--', deviationClass: deviation > 0 ? 'risk' : deviation < 0 ? 'safe' : '' }
    })
    this.setData({ inputRows: rows, matrixGroups: buildMatrixGroups(rows) })
  },
  clearInputs() {
    const inputRows = createInputRows(this.data.dayOptions[this.data.dayOptionIndex], this.data.futureTradeDates)
    this.setData({ inputRows, matrixGroups: buildMatrixGroups(inputRows) })
  },
  openAnnouncement(event) {
    const url = event.currentTarget.dataset.url
    if (!url) return
    if (typeof wx.openUrl === 'function') {
      wx.openUrl({ url, fail: () => this.copyAnnouncementLink(url) })
      return
    }
    this.copyAnnouncementLink(url)
  },
  copyAnnouncementLink(url) {
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '原文链接已复制', icon: 'none' }) })
  },
  onUnload() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
  }
})
