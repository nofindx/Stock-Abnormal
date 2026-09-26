// 小程序启动文件：初始化微信云托管，不收集用户身份信息。
const CLOUD_ENV = 'prod-d1g25zvvkd79d524b'

App({
  onLaunch() {
    if (!wx.cloud) return
    wx.cloud.init({
      env: CLOUD_ENV,
      traceUser: false
    })
  },
  globalData: {
    // 生产环境通过 callContainer 访问云托管，不需要配置 request 合法域名。
    cloudEnv: CLOUD_ENV,
    cloudService: 'flask-9a5y',
    // 本地联调时可临时填写 http://127.0.0.1:8787；留空即使用云托管。
    apiBaseUrl: '',
    // 仅开发者工具 develop 环境使用，便于未完成云环境授权绑定时预览真实数据；体验版和正式版不会读取。
    devApiBaseUrl: 'https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com',
    lastStock: null,
    pendingStock: null,
    lastPredictionRefresh: null
  }
})
