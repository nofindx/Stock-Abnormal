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
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '920229', name: '世纪数码', eventDirection: 'down', marketChange: -5.48 }), false)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '688137', name: '近岸蛋白', eventDirection: 'up', marketChange: -7.79 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '301190', name: '善水科技', eventDirection: 'up', marketChange: -3.35 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '600825', name: '新华传媒', eventDirection: 'up', marketChange: 10.01 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'severe-10d', symbol: '688137', name: '近岸蛋白', marketChange: -7.79 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗', marketChange: 1.25 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗', marketChange: -11.27 }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'ordinary', symbol: '301234', name: '五洲医疗' }), true)
assert.strictEqual(isVisibleMonitorItem({ riskTone: 'delisting-warning', symbol: '301139', name: '*ST元道', marketChange: -10 }), true)

console.log('monitor display-filter tests passed')
