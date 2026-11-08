const assert = require('assert')
global.Page = () => {}
const { normalizeItems, refreshFailureMessage, predictionBoundaryKey } = require('./index')

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
assert.strictEqual(refreshFailureMessage({ refresh_state: 'failed', result_state: 'stale' }),
  '实时行情刷新失败，当前保留上次数据（可能已过期），请手动重试')
assert.strictEqual(refreshFailureMessage({ result_state: 'stale' }),
  '当前显示上次数据（可能已过期），请手动刷新')
assert.notStrictEqual(predictionBoundaryKey(new Date('2026-11-06T08:59:59+08:00')),
  predictionBoundaryKey(new Date('2026-11-06T09:00:00+08:00')))

console.log('prediction page tests passed')
