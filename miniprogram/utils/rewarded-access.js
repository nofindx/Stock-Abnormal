const { client } = require('./ads-config')

function supported(platform, minimum) {
  let version = ''
  try {
    const info = platform.getAppBaseInfo ? platform.getAppBaseInfo() : platform.getSystemInfoSync()
    version = info.SDKVersion || ''
  } catch (_) { return false }
  const numbers = version.split('.').map(Number)
  if (numbers.length < 3 || numbers.some(n => !Number.isFinite(n))) return false
  for (let i = 0; i < 3; i++) {
    if (numbers[i] > minimum[i]) return true
    if (numbers[i] < minimum[i]) return false
  }
  return true
}

function createRewardedAccess({ config = client, platform = () => wx } = {}) {
  const grants = new Set()
  let epoch = 0, disposed = false, busy = null, cancelPending = null
  const grantKey = (feature, key) => JSON.stringify([feature, key])
  const toast = text => {
    const api = platform()
    if (api.showToast) api.showToast({ title: text, icon: 'none' })
  }
  function request(feature, key, isCurrent = () => true) {
    if (disposed || !key) return Promise.resolve(false)
    if (grants.has(grantKey(feature, key))) return Promise.resolve(true)
    if (busy) return busy.feature === feature && busy.key === key ? busy.promise : Promise.resolve(false)
    const attemptEpoch = epoch
    const valid = () => !disposed && epoch === attemptEpoch && isCurrent()
    const task = (async () => {
      let policy = await config.policy(feature)
      if (!valid()) return false
      if (policy.mode === 'free') return true
      const api = platform()
      if (!policy.ready || !policy.unitId || !api.createRewardedVideoAd || !supported(api, [2, 6, 0])) {
        toast('广告暂不可用，请稍后重试'); return false
      }
      const consent = await new Promise(resolve => {
        cancelPending = () => resolve(false)
        if (!api.showModal) return resolve(false)
        api.showModal({ title: '观看视频解锁',
          content: feature === 'simulation_10d' ? '完整观看视频，解锁本次页面的10日计算' : '完整观看视频，解锁本次页面的次日预测（可能暂无候选）',
          confirmText: '观看视频', cancelText: '暂不观看',
          success: result => resolve(result.confirm === true), fail: () => resolve(false) })
      })
      cancelPending = null
      if (!consent || !valid()) return false
      // A confirmation may remain open beyond the configuration TTL.
      policy = await config.policy(feature)
      if (!valid()) return false
      if (policy.mode === 'free') return true
      if (!policy.ready || !policy.unitId) { toast('广告暂不可用，请稍后重试'); return false }
      return new Promise(resolve => {
        let ad, settled = false
        const finish = (earned, message) => {
          if (settled) return
          settled = true
          if (ad) {
            for (const cleanup of [() => ad.offClose && ad.offClose(close),
              () => ad.offError && ad.offError(error), () => ad.destroy && ad.destroy()]) {
              try { cleanup() } catch (_) { /* Cleanup cannot turn a completion into another award. */ }
            }
          }
          cancelPending = null
          const accepted = earned && valid()
          if (accepted) grants.add(grantKey(feature, key))
          if (message && valid()) toast(message)
          resolve(Boolean(accepted))
        }
        const close = result => finish(Boolean(result && result.isEnded === true),
          result && result.isEnded === true ? '' : '尚未完整观看，未解锁')
        const error = () => finish(false, '广告暂不可用，请稍后重试')
        cancelPending = () => finish(false)
        try {
          const options = { adUnitId: policy.unitId }
          if (supported(api, [3, 7, 7])) options.disableFallbackSharePage = true
          ad = api.createRewardedVideoAd(options)
          ad.onClose(close)
          ad.onError(error)
          // Only one bounded reload following this explicit user action.
          Promise.resolve(ad.show()).catch(() => {
            if (!valid() || settled || !ad.load) throw new Error('Ad unavailable')
            return Promise.resolve(ad.load()).then(() => {
              if (!valid() || settled) throw new Error('Ad expired')
              return ad.show()
            })
          }).catch(error)
        } catch (_) { error() }
      })
    })().catch(() => { if (valid()) toast('广告暂不可用，请稍后重试'); return false })
    const pending = { feature, key, promise: null }
    pending.promise = task.finally(() => { if (busy === pending) busy = null })
    busy = pending
    return pending.promise
  }
  return {
    request,
    has: (feature, key) => grants.has(grantKey(feature, key)),
    reset() { epoch++; grants.clear() },
    dispose() { disposed = true; epoch++; grants.clear(); if (cancelPending) cancelPending() }
  }
}
module.exports = { createRewardedAccess, supported }
