const assert=require('assert')
const {request}=require('./request')
async function run(){
  let calls=0,containers=0,url=''
  const app={globalData:{localDevelopmentOnly:true,devApiBaseUrl:'http://127.0.0.1:8787'}}
  global.getApp=()=>app
  global.wx={
    getAccountInfoSync:()=>({miniProgram:{envVersion:'release'}}),
    request(options){calls++;url=options.url;options.success({statusCode:200,data:{code:0,data:{local:true}}})},
    cloud:{callContainer(){containers++}}
  }
  const result=await request({url:'/api/client-config',retry:0})
  assert.strictEqual(result.data.local,true)
  assert.strictEqual(url,'http://127.0.0.1:8787/api/client-config')
  app.globalData.devApiBaseUrl='https://example.test'
  await assert.rejects(request({url:'/api/client-config'}),/本地候选禁止访问云端/)
  assert.strictEqual(calls,1)
  assert.strictEqual(containers,0)
  console.log('local request isolation tests passed: loopback only, no production container fallback')
}
run().catch(e=>{console.error(e);process.exitCode=1})
