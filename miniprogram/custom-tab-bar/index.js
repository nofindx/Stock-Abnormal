// 自定义底部导航：只负责三项页面切换，不承载业务状态。
Component({
  data: {
    selected: 0,
    list: [
      { pagePath: 'pages/stock/index', text: '单股计算', iconPath: '/assets/query.png', selectedIconPath: '/assets/query-active.png' },
      { pagePath: 'pages/monitor/index', text: '重点监控', iconPath: '/assets/monitor.png', selectedIconPath: '/assets/monitor-active.png' },
      { pagePath: 'pages/prediction/index', text: '异动预测', iconPath: '/assets/prediction.png', selectedIconPath: '/assets/prediction-active.png' }
    ]
  },
  methods: {
    switchTab(event) {
      const index = Number(event.currentTarget.dataset.index)
      const item = this.data.list[index]
      if (!item || index === this.data.selected) return
      this.setData({ selected: index })
      wx.switchTab({ url: `/${item.pagePath}` })
    }
  }
})
