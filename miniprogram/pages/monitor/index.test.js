const assert = require('assert')

global.Page = () => {}
const { isVisibleMonitorItem } = require('./index')

assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '920229', name: '世纪数码' }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '002731', name: '*ST萃华' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '510300', name: 'ETF' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '100001', name: '测试证券' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-30d', symbol: '920575', name: 'ST康乐' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-30d', symbol: '600000', name: '某某退' }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'delisting-warning', symbol: '301139', name: '*ST元道' }), true)

console.log('monitor display-filter tests passed')
