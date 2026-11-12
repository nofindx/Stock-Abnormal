// 小程序启动文件：初始化微信云托管，不收集用户身份信息。
const CLOUD_ENV = 'prod-d1g25zvvkd79d524b'
const releaseInfo = require('./release-info')
// 本地开发默认复用既有云端数据。仅启动隔离后端联调时显式设为true，
// 并将devApiBaseUrl设为环回地址；本地开发不等于必须运行本地后端。
const LOCAL_DEVELOPMENT_ONLY = false

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
    // 沿用冻结版：开发者工具读取原云服务；体验/正式环境使用callContainer。
    devApiBaseUrl: 'https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com',
    lastStock: null,
    pendingStock: null,
    lastPredictionRefresh: null
  }
})
