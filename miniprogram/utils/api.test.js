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
  let result = await api.refreshPredictions('today', saved, { reuseContext: true })
  assert.strictEqual(calls.length, 1)
  assert.strictEqual(calls[0].data.read_token, 's1-token')
  assert.strictEqual(result.read_token.value, 's1-token')

  calls = []
  handler = options => {
    if (calls.length === 1) return Promise.reject(Object.assign(new Error('expired'), { code: 40902 }))
    if (options.url === '/api/v2/context') return Promise.resolve({ data: { read_token: { value: 'new-token' } } })
    return Promise.resolve({ data: { ts_code: '600825.SH', read_token: { value: options.data.read_token } } })
  }
  result = await api.refreshStockDetail('600825.SH', saved, { reuseContext: true })
  assert.strictEqual(calls.length, 3)
  assert.strictEqual(result.read_token.value, 'new-token')
  assert.strictEqual(calls[2].data.page, 'stock')

  calls = []
  handler = () => Promise.reject(Object.assign(new Error('scope forbidden'), { code: 40301 }))
  await assert.rejects(api.refreshPredictions('today', saved, { reuseContext: true }), /scope forbidden/)
  assert.strictEqual(calls.length, 1)
  calls = []
  handler = options => options.url === '/api/v2/realtime/refresh'
    ? Promise.resolve({ data: { refresh_state: 'queued', job_id: 'job-a', retry_after_ms: 250 } })
    : Promise.resolve({ data: { items: [{ ts_code: '002724.SZ' }], refresh_state: 'ready', read_token: { value: 's2-token' } } })
  result = await api.refreshPredictions('today', saved, { reuseContext: true })
  assert.strictEqual(calls.length, 2)
  assert.strictEqual(calls[1].url, '/api/v2/jobs/job-a')
  assert.strictEqual(calls[1].data.read_token, 's1-token')
  assert.strictEqual(result.read_token.value, 's2-token')

  calls = []
  handler = options => options.url === '/api/v2/context'
    ? Promise.resolve({ data: { read_token: { value: 'replacement' } } })
    : Promise.reject(Object.assign(new Error('still expired'), { code: 40902 }))
  await assert.rejects(api.refreshPredictions('today', saved, { reuseContext: true }), /still expired/)
  assert.strictEqual(calls.length, 3, 'only one context reacquisition is allowed')

  calls = []
  handler = () => Promise.resolve({ data: {
    items: [{ ts_code: '002724.SZ' }], refresh_state: 'failed', result_state: 'stale',
    reasons: ['REALTIME_REFRESH_FAILED'], read_token: { value: 'fallback-token' }
  } })
  result = await api.refreshPredictions('today', saved, { reuseContext: true })
  assert.strictEqual(result.result_state, 'stale')
  assert.strictEqual(result.items[0].ts_code, '002724.SZ')
  assert.strictEqual(calls.length, 1, 'a failed terminal must not start another job')

  const realSetTimeout = global.setTimeout
  global.setTimeout = callback => { callback(); return 0 }
  try {
    calls = []
    handler = () => Promise.resolve({ data: { refresh_state: 'queued', job_id: 'job-b' } })
    await assert.rejects(api.refreshPredictions('today', saved, { reuseContext: true }), error => error.code === 20201)
    assert.strictEqual(calls.length, 25, 'queued polling has a fixed upper bound')

    calls = []
    handler = options => {
      if (calls.length === 1) return Promise.resolve({ data: { refresh_state: 'queued', job_id: 'job-c' } })
      if (calls.length === 2) return Promise.reject(Object.assign(new Error('expired while polling'), { code: 40902 }))
      if (options.url === '/api/v2/context') return Promise.resolve({ data: { read_token: { value: 'new-poll-context' } } })
      return Promise.resolve({ data: { items: [], refresh_state: 'ready', read_token: { value: 'new-view' } } })
    }
    result = await api.refreshPredictions('today', saved, { reuseContext: true })
    assert.strictEqual(calls.length, 4)
    assert.strictEqual(calls[3].data.read_token, 'new-poll-context')
    assert.strictEqual(result.read_token.value, 'new-view')
  } finally {
    global.setTimeout = realSetTimeout
  }
  for (const page of ['stock', 'prediction']) {
    calls = []
    handler = options => {
      if (options.url === '/api/v2/context') return Promise.resolve({ data: { read_token: { value: 'latest-s1' } } })
      if (options.url === '/api/v2/realtime/refresh') {
        assert.strictEqual(options.data.read_token, 'latest-s1')
        return Promise.resolve({ data: { items: [{ ts_code: 'NEW', change: '+2%' }],
          monitoringFacts: [{ sourceId: 'new-announcement' }], read_token: { value: 'latest-s2' } } })
      }
      assert.strictEqual(options.data.read_token, 'latest-s1')
      return Promise.resolve({ data: { items: [{ ts_code: 'NEW' }], monitoringFacts: [{ sourceId: 'new-announcement' }],
        read_token: { value: 'latest-s1' }, realtimeRefreshAllowed: true } })
    }
    result = page === 'stock' ? await api.refreshStockDetail('600825.SH', saved) : await api.refreshPredictions('today', saved)
    assert.deepStrictEqual(calls.map(call => call.url), ['/api/v2/context',
      page === 'stock' ? '/api/v2/stocks/detail' : '/api/v2/predictions', '/api/v2/realtime/refresh'])
    assert.strictEqual(result.read_token.value, 'latest-s2')
    assert.strictEqual(result.monitoringFacts[0].sourceId, 'new-announcement')
    assert(!calls.some(call => call.data.read_token === 's1-token'), 'manual refresh ignores a still-valid old token')
  }
  calls = []
  handler = options => {
    if (options.url === '/api/v2/context') return Promise.resolve({ data: { read_token: { value: 'newer' } } })
    if (options.url === '/api/v2/realtime/refresh') return Promise.reject(new Error('source unavailable'))
    return Promise.resolve({ data: { items: [{ ts_code: 'NEW' }], read_token: { value: 'newer' }, realtimeRefreshAllowed: true } })
  }
  result = await api.refreshPredictions('today', saved)
  assert.strictEqual(result.items[0].ts_code, 'NEW', 'failed S2 retains latest S1, not the old list')
  assert.strictEqual(result.refresh_state, 'failed')
  assert.strictEqual(result.read_token.value, 'newer')
  calls = []
  handler = () => Promise.reject(new Error('S1 unavailable'))
  await assert.rejects(api.refreshStockDetail('600825.SH', saved), /S1 unavailable/)
  assert.strictEqual(calls.length, 1, 'failed S1 must not request quotes with the old token')
  calls = []
  handler = options => options.url === '/api/v2/context'
    ? Promise.resolve({ data: { read_token: { value: 'closed-next' } } })
    : Promise.resolve({ data: { items: [], nextDayAvailable: false, read_token: { value: 'closed-next' } } })
  result = await api.refreshPredictions('next_day', saved)
  assert.strictEqual(calls.length, 2, 'closed next-day S1 must not start S2')
  assert.strictEqual(result.nextDayAvailable, false)
  for (const page of ['prediction', 'stock']) {
    calls = []
    const fresh = { dataset_id: 'dataset-new', rule_version: 'v76', time_context_id: 'time-new',
      ts_code: '600825.SH', items: [{ ts_code: 'NEW' }], monitoringFacts: [{ sourceId: 'new-notice' }],
      realtimeRefreshAllowed: true, read_token: { value: 'embedded-new' } }
    handler = options => {
      if (options.url === '/api/v2/context') {
        assert.strictEqual(options.data.include_s1, '1')
        return Promise.resolve({ data: { ...fresh, s1: fresh } })
      }
      assert.strictEqual(options.url, '/api/v2/realtime/refresh')
      assert.strictEqual(options.data.read_token, 'embedded-new')
      return Promise.resolve({ data: { ...fresh, refresh_state: 'ready' } })
    }
    result = page === 'stock' ? await api.refreshStockDetail('600825.SH', saved) : await api.refreshPredictions('today', saved)
    assert.deepStrictEqual(calls.map(call => call.url), ['/api/v2/context', '/api/v2/realtime/refresh'])
    assert.strictEqual(result.monitoringFacts[0].sourceId, 'new-notice')
    calls = []
    handler = options => options.url === '/api/v2/context'
      ? Promise.resolve({ data: { ...fresh, s1: fresh } })
      : Promise.reject(new Error('S2 failed'))
    result = page === 'stock' ? await api.refreshStockDetail('600825.SH', saved) : await api.refreshPredictions('today', saved)
    assert.strictEqual(result.read_token.value, 'embedded-new', 'failed S2 keeps the embedded latest S1')
    assert.strictEqual(result.result_state, 'stale')
    calls = []
    handler = () => Promise.resolve({ data: { ...fresh, s1: { ...fresh, dataset_id: 'wrong' } } })
    await assert.rejects(page === 'stock' ? api.getStockDetail('600825.SH') : api.getPredictions('today'), /授权范围不一致/)
    assert.strictEqual(calls.length, 1, 'mismatched embedded S1 must not proceed to S2')
  }
  calls = []
  handler = () => Promise.resolve({ data: { dataset_id: 'closed', read_token: { value: 'closed' },
    s1: { dataset_id: 'closed', read_token: { value: 'closed' }, items: [], nextDayAvailable: false } } })
  result = await api.refreshPredictions('next_day', saved)
  assert.strictEqual(result.nextDayAvailable, false)
  assert.strictEqual(calls.length, 1, 'embedded closed-next S1 never starts S2')
  for (const page of ['stock', 'prediction']) {
    calls = []
    const fresh = { ts_code: '600825.SH', dataset_id: 'new-list-and-facts',
      read_token: { value: 'progress-s1' }, realtimeRefreshAllowed: true,
      items: [{ ts_code: 'NEW' }], monitoringFacts: [{ sourceId: 'new-fact-before-quotes' }] }
    let finishQuotes
    let shownS1 = null
    handler = options => options.url === '/api/v2/context'
      ? Promise.resolve({ data: { ...fresh, s1: fresh } })
      : new Promise(resolve => { finishQuotes = resolve })
    const options = { onS1: value => { shownS1 = value } }
    const pending = page === 'stock' ? api.refreshStockDetail('600825.SH', saved, options) : api.refreshPredictions('today', saved, options)
    for (let i = 0; i < 10 && !finishQuotes; i++) await Promise.resolve()
    assert(finishQuotes, 'S2 has started after S1 acquisition')
    assert.strictEqual(shownS1.monitoringFacts[0].sourceId, 'new-fact-before-quotes', 'latest facts can render before a slow S2 completes')
    assert.strictEqual(shownS1.read_token.value, 'progress-s1')
    finishQuotes({ data: { ...fresh, refresh_state: 'ready', refreshStatus: 'applied' } })
    await pending
  }
  console.log('API context reuse and expiry recovery tests passed')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
