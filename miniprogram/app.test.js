const assert = require('assert')
const fs = require('fs')
const path = require('path')
const vm = require('vm')

// 微信运行时不像 Node 一样支持 require(JSON)。使用仅支持 JS 的加载器，
// 从真实 app.js 执行启动，避免打包成功掩盖 App 尚未注册的错误。
function boot(envVersion) {
  const root = __dirname
  let definition
  const cloudCalls = []
  const cache = new Map()
  const context = vm.createContext({
    App(value) { definition = value },
    wx: {
      getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
      cloud: { init: value => cloudCalls.push(value) }
    }
  })
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports
    const module = { exports: {} }
    cache.set(file, module)
    const source = fs.readFileSync(file, 'utf8')
    const execute = vm.runInContext(`(function(require, module, exports) {\n${source}\n})`, context, { filename: file })
    execute(request => {
      assert(request.startsWith('.'), 'App dependencies must be local modules')
      assert(!path.extname(request) || path.extname(request) === '.js', `Unsupported mini-program module: ${request}`)
      return load(path.resolve(path.dirname(file), `${request}${path.extname(request) ? '' : '.js'}`))
    }, module, module.exports)
    return module.exports
  }
  load(path.join(root, 'app.js'))
  assert(definition, 'App must register before pages can start')
  definition.onLaunch()
  assert.deepStrictEqual(JSON.parse(JSON.stringify(definition.globalData.releaseInfo)), JSON.parse(fs.readFileSync(path.join(root, 'release-info.json'), 'utf8')))
  assert.strictEqual(definition.globalData.cloudService, 'flask-9a5y')
  assert.match(definition.globalData.devApiBaseUrl, /^https:\/\//)
  assert.strictEqual(cloudCalls.length, envVersion === 'develop' ? 0 : 1)
  if (cloudCalls.length) assert.strictEqual(cloudCalls[0].traceUser, false)
}

boot('develop')
boot('trial')
boot('release')
console.log('mini-program JS-only App startup tests passed (develop/trial/release)')
