// 小程序启动文件：初始化微信云托管，不收集用户身份信息。
const CLOUD_ENV = 'prod-d1g25zvvkd79d524b'
const releaseInfo = require('./release-info')
// 此分支目前只做本地联调。正式提审前需显式退出本地模式并另行验收。
const LOCAL_DEVELOPMENT_ONLY = true

App({
  onLaunch() {
    let envVersion = ''
    try {
      envVersion = wx.getAccountInfoSync().miniProgram.envVersion || ''
    } catch (error) {
      // 旧版基础库没有 getAccountInfoSync 时，按生产环境处理。
    }
    // 开发者工具走 devApiBaseUrl，跳过云托管初始化，避免云环境探测阻塞 appLaunch。
    if (wx.cloud && envVersion !== 'develop' && !LOCAL_DEVELOPMENT_ONLY) {
      wx.cloud.init({
        env: CLOUD_ENV,
        traceUser: false
      })
    }
  },
  globalData: {
    releaseInfo,
    localDevelopmentOnly: LOCAL_DEVELOPMENT_ONLY,
    // 生产环境通过 callContainer 访问云托管，不需要配置 request 合法域名。
    cloudEnv: CLOUD_ENV,
    cloudService: 'flask-9a5y',
    // 本地联调时可临时填写 http://127.0.0.1:8787；留空即使用云托管。
    apiBaseUrl: '',
    // 本地候选在所有环境仅访问环回服务；后续获准发布时再恢复原云托管入口并验收。
    devApiBaseUrl: 'http://127.0.0.1:8787',
    lastStock: null,
    pendingStock: null,
    lastPredictionRefresh: null
  }
})
