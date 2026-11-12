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
  // Exercise the actual App defaults so a disconnected localhost cannot silently
  // become the default for all developer tools again.
  global.App=value=>{value.onLaunch();Object.assign(app,value)}
  wx.getAccountInfoSync=()=>({miniProgram:{envVersion:'develop'}})
  require('../app')
  await request({url:'/api/stocks/search',data:{q:'新华'},retry:0})
  assert(url.startsWith('https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com/api/stocks/search?'))
  assert.strictEqual(containers,0,'developer tools skip cloud probing and use existing HTTPS')
  wx.getAccountInfoSync=()=>({miniProgram:{envVersion:'trial'}})
  wx.cloud.callContainer=options=>{
    containers++;assert.strictEqual(options.config.env,app.globalData.cloudEnv)
    options.success({statusCode:200,data:{code:0,data:{cloud:true}}})
  }
  const cloudResult=await request({url:'/api/monitor',retry:0})
  assert.strictEqual(cloudResult.data.cloud,true)
  assert.strictEqual(containers,1,'trial retains the original cloud route')
  console.log('request route tests passed: explicit loopback isolation; actual App defaults use cloud HTTPS in develop and callContainer in trial')
}
run().catch(e=>{console.error(e);process.exitCode=1})
