// 统一请求层：生产使用微信云托管，本地开发可切换到 wx.request。

function request(options) {
  const { url, method = 'GET', data = {}, timeout = 8000, retry = 1 } = options
  const app = getApp()
  const globalData = (app && app.globalData) || {}
  let baseUrl = globalData.apiBaseUrl || ''
  if (!baseUrl && globalData.devApiBaseUrl && wx.getAccountInfoSync) {
    try {
      const envVersion = wx.getAccountInfoSync().miniProgram.envVersion
      if (envVersion === 'develop') baseUrl = globalData.devApiBaseUrl
    } catch (error) {
      // 部分旧基础库没有 getAccountInfoSync，继续使用云托管调用。
    }
  }
  return new Promise((resolve, reject) => {
    let attempts = 0
    const send = () => {
      attempts += 1
      const query = method === 'GET' && data && Object.keys(data).length
        ? `?${Object.keys(data).filter((key) => data[key] !== undefined && data[key] !== null).map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(data[key])}`).join('&')}`
        : ''
      const requestPath = `${url}${query}`
      const handleSuccess = (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300 && response.data && response.data.code === 0) {
          resolve(response.data)
          return
        }
        reject(new Error((response.data && (response.data.message || response.data.errorMsg)) || `HTTP ${response.statusCode}`))
      }
      const handleFail = (error) => {
        if (attempts <= retry) {
          send()
          return
        }
        reject(error)
      }

      if (baseUrl) {
        wx.request({
          url: `${baseUrl}${requestPath}`,
          method,
          data,
          timeout,
          success: handleSuccess,
          fail: handleFail
        })
        return
      }

      if (!wx.cloud || !wx.cloud.callContainer) {
        reject(new Error('当前基础库不支持微信云托管'))
        return
      }
      wx.cloud.callContainer({
        config: { env: globalData.cloudEnv },
        path: requestPath,
        header: {
          'X-WX-SERVICE': globalData.cloudService,
          'content-type': 'application/json'
        },
        method,
        data: method === 'GET' ? {} : data,
        timeout,
        success: handleSuccess,
        fail: handleFail
      })
    }
    send()
  })
}

module.exports = { request }
