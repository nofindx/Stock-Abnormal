// 小程序启动文件：初始化微信云托管，不收集用户身份信息。
const CLOUD_ENV = 'prod-d1g25zvvkd79d524b'

App({
  onLaunch() {
    let envVersion = ''
    try {
      envVersion = wx.getAccountInfoSync().miniProgram.envVersion || ''
    } catch (error) {
      // 旧版基础库没有 getAccountInfoSync 时，按生产环境处理。
    }
    // 开发者工具走 devApiBaseUrl，跳过云托管初始化，避免云环境探测阻塞 appLaunch。
    if (wx.cloud && envVersion !== 'develop') {
      wx.cloud.init({
        env: CLOUD_ENV,
        traceUser: false
      })
    }
  },
  globalData: {
    // 生产环境通过 callContainer 访问云托管，不需要配置 request 合法域名。
    cloudEnv: CLOUD_ENV,
    cloudService: 'flask-9a5y',
    // 本地联调时可临时填写 http://127.0.0.1:8787；留空即使用云托管。
    apiBaseUrl: '',
    // 仅开发者工具 develop 环境使用，上传的开发版本也能直接访问真实数据；体验版和正式版不会读取。
    // 本地联调时临时改为 http://127.0.0.1:8787，验收云端后恢复此地址再上传。
    devApiBaseUrl: 'https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com',
    lastStock: null,
    pendingStock: null,
    lastPredictionRefresh: null
  }
})
