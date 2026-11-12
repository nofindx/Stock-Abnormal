const assert = require('assert')
const { createRewardedAccess } = require('./rewarded-access')
const tick = async () => { for(let i=0;i<12;i++) await Promise.resolve() }
function setup(options = {}) {
  const ads = [], messages = []
  let modals = 0
  const api = {
    getAppBaseInfo: () => ({SDKVersion:'3.8.8'}),
    showToast: value => messages.push(value.title),
    showModal: value => { modals++; value.success({confirm:options.consent !== false}) },
    createRewardedVideoAd: () => {
      const ad = { closes:[], errors:[], showCount:0, destroyed:0,
        onClose(fn){this.closes.push(fn)}, offClose(fn){this.closes=this.closes.filter(x=>x!==fn)},
        onError(fn){this.errors.push(fn)}, offError(fn){this.errors=this.errors.filter(x=>x!==fn)},
        destroy(){this.destroyed++},
        show(){this.showCount++;return options.showFail ? Promise.reject(Error('show')) : Promise.resolve()},
        load(){return options.loadFail ? Promise.reject(Error('load')) : Promise.resolve()},
        close(result){this.closes.slice().forEach(fn=>fn(result))}
      }
      ads.push(ad);return ad
    }
  }
  const controller = createRewardedAccess({
    config:{policy: async () => options.policy || {mode:'rewarded',ready:true,unitId:'adunit-test'}},
    platform:()=>api
  })
  return {controller,ads,messages,api,modals:()=>modals}
}
async function run() {
  const s = setup()
  const first = s.controller.request('simulation_10d','stock-A')
  assert.strictEqual(s.controller.request('simulation_10d','stock-A'),first,'duplicate click coalesces')
  await tick()
  assert.strictEqual(s.ads.length,1)
  assert.strictEqual(s.ads[0].showCount,1)
  const callback = s.ads[0].closes[0]
  s.ads[0].close({isEnded:true})
  assert.strictEqual(await first,true)
  callback({isEnded:true})
  assert.strictEqual(s.ads[0].destroyed,1)
  assert.strictEqual(await s.controller.request('simulation_10d','stock-A'),true)
  assert.strictEqual(s.ads.length,1,'same session grant reused')
  const other = s.controller.request('prediction_next','20261113');await tick()
  assert.strictEqual(s.ads.length,2,'other feature needs separate video')
  s.ads[1].close({isEnded:false});assert.strictEqual(await other,false)
  assert.strictEqual(s.controller.has('prediction_next','20261113'),false)
  assert.strictEqual(s.controller.has('simulation_10d','stock-A'),true)
  for(const result of [undefined,{}, {isEnded:1}, {isEnded:false}]) {
    const t=setup(),p=t.controller.request('simulation_10d','key')
    await tick();t.ads[0].close(result);assert.strictEqual(await p,false)
  }
  const reset = setup(), pending=reset.controller.request('simulation_10d','old')
  await tick();reset.controller.reset();reset.ads[0].close({isEnded:true})
  assert.strictEqual(await pending,false,'reset invalidates late success')
  assert.strictEqual(reset.controller.has('simulation_10d','old'),false)
  const disposed=setup(),dp=disposed.controller.request('prediction_next','date')
  await tick();const late=disposed.ads[0].closes[0];disposed.controller.dispose()
  assert.strictEqual(await dp,false);late({isEnded:true});assert.strictEqual(disposed.controller.has('prediction_next','date'),false)
  const cancel=setup({consent:false})
  assert.strictEqual(await cancel.controller.request('simulation_10d','key'),false)
  assert.strictEqual(cancel.ads.length,0)
  const failure=setup({showFail:true,loadFail:true})
  assert.strictEqual(await failure.controller.request('simulation_10d','key'),false)
  assert.strictEqual(failure.ads.length,1)
  assert.strictEqual(failure.ads[0].showCount,1)
  const missing=setup({policy:{mode:'rewarded',ready:false}})
  assert.strictEqual(await missing.controller.request('simulation_10d','key'),false)
  assert.strictEqual(missing.ads.length,0)
  const free=setup({policy:{mode:'free',ready:false}})
  assert.strictEqual(await free.controller.request('simulation_10d','key'),true)
  assert.strictEqual(free.ads.length,0)
  const unsupported=setup()
  unsupported.api.getAppBaseInfo=()=>({SDKVersion:'2.5.0'})
  assert.strictEqual(await unsupported.controller.request('simulation_10d','key'),false)
  const stale=setup();let current=true
  const sp=stale.controller.request('simulation_10d','key',()=>current)
  await tick();current=false;stale.ads[0].close({isEnded:true})
  assert.strictEqual(await sp,false)
  console.log('rewarded access tests passed: full/partial, duplicate, failures, reset/dispose, independent scopes, compatibility')
}
run().catch(e=>{console.error(e);process.exitCode=1})
