// Advertising config only. No authorization token, persistence, market reads or retry timer.
const { request } = require('./request')
const release = require('../release-info')
const placements = ['home_history_below', 'prediction_footer', 'stock_simulation_above', 'monitor_footer']
const features = ['simulation_10d', 'prediction_next']

function validate(value) {
  const keysEqual = (object, keys) => object && typeof object === 'object' &&
    Object.keys(object).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(object, key))
  const ads = value && value.ads
  const gates = value && value.featureGates
  if (!keysEqual(value, ['configVersion', 'ads', 'featureGates']) ||
      typeof value.configVersion !== 'string' || !/^[A-Za-z0-9._-]{1,80}$/.test(value.configVersion) ||
      !keysEqual(ads, ['enabled', 'allowedVersions', 'cacheTtlSeconds', 'placements', 'rewarded']) || !gates ||
      typeof ads.enabled !== 'boolean' || ads.cacheTtlSeconds !== 600 ||
      !Array.isArray(ads.allowedVersions) || !ads.allowedVersions.length || ads.allowedVersions.length > 20 ||
      !ads.allowedVersions.every(v => typeof v === 'string' && /^\d+\.\d+\.\d+$/.test(v))) throw new Error('Invalid ad config')
  for (const [group, keys, type] of [['placements', placements, 'ad-custom'], ['rewarded', features, 'rewarded-video']]) {
    if (!ads[group] || Object.keys(ads[group]).length !== keys.length) throw new Error('Invalid ad slots')
    for (const key of keys) {
      const slot = ads[group][key]
      if (!keysEqual(slot, ['enabled', 'type', 'unitId']) || typeof slot.enabled !== 'boolean' || slot.type !== type || typeof slot.unitId !== 'string' ||
          (slot.unitId && !/^adunit-[A-Za-z0-9]{1,64}$/.test(slot.unitId)) || (slot.enabled && !slot.unitId)) throw new Error('Invalid ad slot')
    }
  }
  if (!keysEqual(gates, features)) throw new Error('Invalid gates')
  for (const key of features) {
    const mode = gates[key] && gates[key].mode
    if (!keysEqual(gates[key], ['mode']) || !['free', 'rewarded'].includes(mode) ||
        (mode === 'rewarded' && (!ads.enabled || !ads.rewarded[key].enabled))) throw new Error('Invalid feature gate')
  }
  return value
}

function createConfigClient({ fetcher, version, now = Date.now }) {
  let value = null, expiresAt = 0, retryAt = 0, inflight = null
  const knownModes = {}
  function get() {
    if (value && now() < expiresAt) return Promise.resolve(value)
    if (inflight) return inflight
    if (now() < retryAt) return Promise.resolve(null)
    inflight = Promise.resolve().then(fetcher).then(raw => {
      const config = validate(raw)
      if (!config.ads.allowedVersions.includes(version)) throw new Error('Version not enabled')
      value = config
      expiresAt = now() + 600000
      retryAt = 0
      features.forEach(key => { knownModes[key] = config.featureGates[key].mode })
      return value
    }).catch(() => {
      expiresAt = 0
      retryAt = now() + 60000
      return null
    }).finally(() => { inflight = null })
    return inflight
  }
  return {
    get,
    async placement(key) {
      const config = await get()
      const slot = config && config.ads.placements[key]
      return config && config.ads.enabled && slot && slot.enabled ? { ...slot } : null
    },
    async policy(key) {
      if (!features.includes(key)) return { mode: 'rewarded', ready: false }
      const config = await get()
      if (!config) return { mode: knownModes[key] || 'free', ready: false }
      const mode = config.featureGates[key].mode
      const slot = config.ads.rewarded[key]
      return { mode, ready: mode === 'free' || Boolean(config.ads.enabled && slot.enabled), unitId: slot.unitId }
    }
  }
}

const client = createConfigClient({
  version: release.miniProgramVersion,
  fetcher: () => request({ url: '/api/client-config', timeout: 4000, retry: 0 }).then(result => result.data)
})
module.exports = { client, createConfigClient, validate }
