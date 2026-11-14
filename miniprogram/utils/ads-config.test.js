const assert = require('assert')
const { createConfigClient, validate } = require('./ads-config')
const releaseVersion = require('../release-info').miniProgramVersion
function config() {
  return { configVersion: 'test', ads: { enabled: false, allowedVersions: [...new Set(['2.0.0', releaseVersion])], cacheTtlSeconds: 600,
    placements: Object.fromEntries(['home_history_below','prediction_footer','stock_simulation_above','monitor_footer'].map(k => [k, { enabled: false, type: 'ad-custom', unitId: '' }])),
    rewarded: Object.fromEntries(['simulation_10d','prediction_next'].map(k => [k, { enabled: false, type: 'rewarded-video', unitId: '' }])) },
    featureGates: { simulation_10d: { mode: 'free' }, prediction_next: { mode: 'free' } } }
}
async function run() {
  let clock = 0, calls = 0, offline = false
  const value = config()
  const client = createConfigClient({ version: releaseVersion, now: () => clock, fetcher: async () => { calls++; if (offline) throw Error('offline'); return value } })
  await Promise.all([client.get(),client.get(),client.policy('simulation_10d')])
  assert.strictEqual(calls, 1)
  assert.strictEqual((await client.policy('simulation_10d')).mode, 'free')
  clock = 599999
  await client.get()
  assert.strictEqual(calls, 1)
  value.ads.enabled = true
  value.ads.rewarded.simulation_10d = { enabled: true, type: 'rewarded-video', unitId: 'adunit-test1' }
  value.featureGates.simulation_10d.mode = 'rewarded'
  clock = 600000
  assert.strictEqual((await client.policy('simulation_10d')).ready, true)
  assert.strictEqual(calls, 2)
  offline = true
  clock = 1200000
  const failed = await client.policy('simulation_10d')
  assert.deepStrictEqual(failed, { mode: 'rewarded', ready: false })
  await client.get()
  assert.strictEqual(calls, 3, 'failure backoff prevents retry storm')
  clock += 60000
  await client.get()
  assert.strictEqual(calls, 4)
  const initialFailure = createConfigClient({version:'2.0.0',fetcher:async()=>{throw Error('offline')}})
  assert.deepStrictEqual(await initialFailure.policy('prediction_next'),{mode:'free',ready:false})
  const mismatch = createConfigClient({version:'9.0.0',fetcher:async()=>config()})
  assert.strictEqual(await mismatch.get(),null)
  assert.strictEqual(await client.placement('unknown'),null)
  const malformed = config()
  malformed.ads.placements.monitor_footer.enabled = true
  assert.throws(()=>validate(malformed))
  malformed.ads.placements.monitor_footer.enabled = false
  malformed.featureGates.prediction_next.mode = 'rewarded'
  assert.throws(()=>validate(malformed))
  console.log('ad config tests passed: cache, single-flight, backoff, fail-closed known gates, defaults, malformed/version')
}
if (require.main === module) run().catch(e=>{console.error(e);process.exitCode=1})
module.exports = { config }
