const assert = require('assert')
const fs = require('fs')
const path = require('path')
const template = fs.readFileSync(path.join(__dirname, 'index.wxml'), 'utf8')
assert(template.includes('item.pdfAvailable && item.sourceUrl'))
assert(!template.includes('item.pdfCached && item.sourceUrl'))

let pageDefinition
global.Page = (definition) => { pageDefinition = definition }
const requests = []
const apiPath = require.resolve('../../utils/api')
require.cache[apiPath] = {
  id: apiPath,
  filename: apiPath,
  loaded: true,
  exports: {
    getMonitor: (options) => new Promise((resolve, reject) => requests.push({ options, resolve, reject }))
  }
}
const { isVisibleMonitorItem } = require('./index')

assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '920229', name: '世纪数码' }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '002731', name: '*ST萃华' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '510300', name: 'ETF' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '100001', name: '测试证券' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-30d', symbol: '920575', name: 'ST康乐' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-30d', symbol: '600000', name: '某某退' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'delisting-warning', symbol: '301139', name: '*ST元道' }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '920229', name: '世纪数码', eventDirection: 'down', marketChange: -5.48 }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '688137', name: '近岸蛋白', eventDirection: 'up', marketChange: -7.79 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '301190', name: '善水科技', eventDirection: 'up', marketChange: -3.35 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '600825', name: '新华传媒', eventDirection: 'up', marketChange: 10.01 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '688137', name: '近岸蛋白', marketChange: -7.79 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗', marketChange: 1.25 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗', marketChange: -11.27 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗' }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'delisting-warning', symbol: '301139', name: '*ST元道', marketChange: -10 }), true)

async function testTabSwitchDropsOldResponseAndDoesNotRestoreStaleCache() {
  const page = Object.assign({}, pageDefinition, {
    data: JSON.parse(JSON.stringify(pageDefinition.data)),
    setData(patch, callback) {
      this.data = { ...this.data, ...patch }
      if (callback) callback()
    }
  })

  page.onLoad()
  assert.strictEqual(requests.length, 1)
  assert.strictEqual(requests[0].options.type, 'all')

  page.selectType({ currentTarget: { dataset: { type: 'risk' } } })
  assert.strictEqual(requests.length, 2, 'tab switch starts a new request even while the previous one is pending')
  assert.strictEqual(requests[1].options.type, 'risk')
  assert.deepStrictEqual(page.data.items, [], 'old-tab countdown is removed while new data loads')

  requests[0].resolve({ items: [{ ts_code: '301313.SZ', symbol: '301313', days: 7 }], updatedAt: '2026-11-04 16:00' })
  await Promise.resolve()
  assert.deepStrictEqual(page.data.items, [], 'late response for the previous tab cannot restore stale items')

  requests[1].resolve({ items: [{ ts_code: '301313.SZ', symbol: '301313', days: 1 }], updatedAt: '2026-11-05 09:00' })
  await Promise.resolve()
  assert.strictEqual(page.data.items[0].days, '01')
}

testTabSwitchDropsOldResponseAndDoesNotRestoreStaleCache().then(() => {
  console.log('monitor tab-switch freshness tests passed')
}).catch((error) => {
  console.error(error)
  process.exitCode = 1
})

console.log('monitor display-filter tests passed')
