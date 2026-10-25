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

console.log('prediction page tests passed')
