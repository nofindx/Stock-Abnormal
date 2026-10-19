const assert = require('assert')

global.Page = () => {}
const { isIntradayRefreshResult } = require('./index')

assert.strictEqual(isIntradayRefreshResult({ refreshStatus: 'applied', dataStage: 'realtime', dataQuality: { intraday: true } }), true)
assert.strictEqual(isIntradayRefreshResult({ refreshStatus: 'applied', dataStage: 'formal', dataQuality: { intraday: false } }), false)
assert.strictEqual(isIntradayRefreshResult({ refreshStatus: 'not_allowed', dataStage: 'formal', dataQuality: { intraday: false } }), false)
assert.strictEqual(isIntradayRefreshResult({ refreshStatus: 'fallback', dataStage: 'formal', dataQuality: { intraday: false } }), false)

console.log('prediction first-open tests passed')
