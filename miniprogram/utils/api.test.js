const assert = require('assert')
const Module = require('module')
const originalLoad = Module._load
let calls = []
let handler
Module._load = function (name, parent, isMain) {
  if (name === './request' && parent.filename.endsWith('/utils/api.js')) {
    return { request: options => { calls.push(options); return handler(options) } }
  }
  return originalLoad.apply(this, arguments)
}
const api = require('./api')
Module._load = originalLoad

async function run() {
  const saved = { read_token: { value: 's1-token' } }
  handler = options => Promise.resolve({ data: { items: [], read_token: { value: options.data.read_token } } })
  let result = await api.refreshPredictions('today', saved)
  assert.strictEqual(calls.length, 1)
  assert.strictEqual(calls[0].data.read_token, 's1-token')
  assert.strictEqual(result.read_token.value, 's1-token')

  calls = []
  handler = options => {
    if (calls.length === 1) return Promise.reject(Object.assign(new Error('expired'), { code: 40902 }))
    if (options.url === '/api/v2/context') return Promise.resolve({ data: { read_token: { value: 'new-token' } } })
    return Promise.resolve({ data: { ts_code: '600825.SH', read_token: { value: options.data.read_token } } })
  }
  result = await api.refreshStockDetail('600825.SH', saved)
  assert.strictEqual(calls.length, 3)
  assert.strictEqual(result.read_token.value, 'new-token')
  assert.strictEqual(calls[2].data.page, 'stock')

  calls = []
  handler = () => Promise.reject(Object.assign(new Error('scope forbidden'), { code: 40301 }))
  await assert.rejects(api.refreshPredictions('today', saved), /scope forbidden/)
  assert.strictEqual(calls.length, 1)
  calls = []
  handler = options => options.url === '/api/v2/realtime/refresh'
    ? Promise.resolve({ data: { refresh_state: 'queued', job_id: 'job-a', retry_after_ms: 250 } })
    : Promise.resolve({ data: { items: [{ ts_code: '002724.SZ' }], refresh_state: 'ready', read_token: { value: 's2-token' } } })
  result = await api.refreshPredictions('today', saved)
  assert.strictEqual(calls.length, 2)
  assert.strictEqual(calls[1].url, '/api/v2/jobs/job-a')
  assert.strictEqual(calls[1].data.read_token, 's1-token')
  assert.strictEqual(result.read_token.value, 's2-token')
  console.log('API context reuse and expiry recovery tests passed')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
