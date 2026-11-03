const assert = require('assert')
global.Page = () => {}
const { normalizeItems } = require('./index')

const triggered = normalizeItems([{
  ts_code: '301313.SZ',
  name: '凡拓数创',
  ruleKey: 'severe_10d',
  triggered: true,
  cardTone: 'triggered',
  triggerValue: '+100.00%'
}], 'today')

assert.strictEqual(triggered.length, 1)
assert.strictEqual(triggered[0].triggered, true)
assert.strictEqual(triggered[0].cardTone, 'triggered')
assert.strictEqual(triggered[0].riskTone, 'severe')
assert.strictEqual(triggered[0].alertText, '已触发')
assert.strictEqual(triggered[0].todayTriggered, true)

const ordinary = normalizeItems([{
  ts_code: '603028.SH',
  name: '赛福天',
  ruleKey: 'ordinary_3d',
  triggered: false
}], 'next_day')
assert.strictEqual(ordinary[0].riskTone, 'ordinary')

const nextDay = normalizeItems([{
  ts_code: '301313.SZ',
  name: '凡拓数创',
  ruleKey: 'severe_30d',
  triggered: false,
  triggerValue: '+200.00%'
}], 'next_day')
assert.strictEqual(nextDay.length, 1)
assert.strictEqual(nextDay[0].showCurrentChange, false)
assert.strictEqual(nextDay[0].triggered, false)
assert.strictEqual(nextDay[0].todayTriggered, false)

const nextDayAlreadyAtThreshold = normalizeItems([{
  ts_code: '301313.SZ',
  ruleKey: 'severe_30d',
  triggered: true,
  cardTone: 'triggered'
}], 'next_day')
assert.strictEqual(nextDayAlreadyAtThreshold[0].triggered, true)
assert.strictEqual(nextDayAlreadyAtThreshold[0].todayTriggered, false)

const negativeUpwardBoundary = normalizeItems([{
  ts_code: '300949.SZ',
  name: '奥雅股份',
  ruleKey: 'severe_10d',
  change: '-0.75%',
  changeSource: 'realtime',
  deviationLabel: '10日 +99.03%',
  triggerValue: '-0.24%',
  triggered: false,
}], 'today')[0]
assert.strictEqual(negativeUpwardBoundary.triggerValue, '-0.24%')
assert.strictEqual(negativeUpwardBoundary.triggered, false)
assert.strictEqual(negativeUpwardBoundary.todayTriggered, false)
assert.strictEqual(negativeUpwardBoundary.cardTone, '')
assert.strictEqual(negativeUpwardBoundary.riskTone, 'severe')

console.log('prediction page tests passed')
