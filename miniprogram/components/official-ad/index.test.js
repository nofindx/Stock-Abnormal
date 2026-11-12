const assert = require('assert')
const { client } = require('../../utils/ads-config')
let definition
global.Component = value => { definition = value }
global.wx = { getAppBaseInfo: () => ({SDKVersion:'3.8.8'}) }
require('./index')
function component() {
  return { ...definition.methods, properties: { placement: 'monitor_footer', allowed: true },
    data: JSON.parse(JSON.stringify(definition.data)), setData(values) { Object.assign(this.data,values) } }
}
const tick = async()=>{for(let i=0;i<8;i++)await Promise.resolve()}
async function run() {
  let calls=0, enabled=true
  client.placement=async()=>{calls++;return enabled?{unitId:'adunit-test'}:null}
  const c=component()
  definition.lifetimes.attached.call(c);await tick()
  assert.strictEqual(c.data.mounted,true)
  const id=c.data.unitId
  c.onAdLoad();assert.strictEqual(c.data.loaded,true)
  c.onAdError();assert.strictEqual(c.data.failed,true)
  c.onAdLoad();assert.strictEqual(c.data.failed,false,'official late recovery load is allowed')
  definition.pageLifetimes.hide.call(c)
  assert.strictEqual(c.data.foreground,false)
  definition.pageLifetimes.show.call(c);await tick()
  assert.strictEqual(c.data.unitId,id)
  assert.strictEqual(c._attempted,true,'page show cannot remount')
  enabled=false
  await c.checkConfig()
  assert.strictEqual(c.data.failed,true)
  c.onAdLoad();assert.strictEqual(c.data.failed,true,'late callback cannot override config shutdown')
  const values={...c.data}
  definition.lifetimes.detached.call(c)
  c.onAdLoad();c.onAdError()
  assert.deepStrictEqual(c.data,values,'detached callbacks ignored')
  const d=component();d.properties.allowed=false
  const before=calls
  definition.lifetimes.attached.call(d);await tick()
  assert.strictEqual(calls,before,'no content, no config/ad attempt')
  d.properties.allowed=true;enabled=true
  await d.checkConfig();assert.strictEqual(d.data.mounted,true)
  const e=component()
  let finish
  client.placement=()=>new Promise(resolve=>{finish=resolve})
  definition.lifetimes.attached.call(e)
  definition.lifetimes.detached.call(e)
  finish({unitId:'adunit-old'});await tick()
  assert.strictEqual(e.data.mounted,false,'late config cannot mount detached component')
  console.log('official ad component tests passed: visibility, one mount, config shutdown, late load/error/config, content')
}
run().catch(e=>{console.error(e);process.exitCode=1})
