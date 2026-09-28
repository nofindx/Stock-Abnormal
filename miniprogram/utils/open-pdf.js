// 公告 PDF 打开工具：只使用临时文件，不写入小程序永久存储。
function openPdf(url, options = {}) {
  if (!url) return Promise.reject(new Error('公告地址为空'))

  const showError = typeof options.onError === 'function' ? options.onError : () => {}
  if (typeof wx.downloadFile !== 'function' || typeof wx.openDocument !== 'function') {
    const error = new Error('当前基础库不支持 PDF 打开')
    showError(error)
    return Promise.reject(error)
  }

  wx.showLoading({ title: '正在打开', mask: true })
  const finish = () => wx.hideLoading()
  const fail = (error) => {
    finish()
    showError(error instanceof Error ? error : new Error('公告暂时无法打开'))
  }

  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      timeout: 30000,
      success: (response) => {
        if (!response || (response.statusCode && response.statusCode !== 200) || !response.tempFilePath) {
          const error = new Error('公告 PDF 下载失败')
          fail(error)
          reject(error)
          return
        }
        wx.openDocument({
          filePath: response.tempFilePath,
          fileType: 'pdf',
          showMenu: true,
          success: () => {
            finish()
            resolve()
          },
          fail: (error) => {
            fail(error)
            reject(error)
          }
        })
      },
      fail: (error) => {
        fail(error)
        reject(error)
      }
    })
  })
}

module.exports = { openPdf }
