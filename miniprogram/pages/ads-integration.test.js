const assert=require('assert')
const fs=require('fs')
const path=require('path')
const api=require('../utils/api')
const {client}=require('../utils/ads-config')
let definition
global.Page=v=>{definition=v}
const app={globalData:{}}
global.getApp=()=>app
let advertisements=[], notices=[]
global.wx={
  getStorageSync:()=>[],setStorageSync(){},removeStorageSync(){},
  showToast:v=>notices.push(v.title),getAppBaseInfo:()=>({SDKVersion:'3.8.8'}),
  showModal:v=>v.success({confirm:true}),
  createRewardedVideoAd:()=>{
    const ad={close:null,onClose(fn){this.close=fn},offClose(){},onError(){},offError(){},destroy(){},
      show(){return Promise.resolve()},load(){return Promise.resolve()}}
    advertisements.push(ad);return ad
  }
}
function page(name){
  delete require.cache[require.resolve(name)]
  require(name)
  return {...definition,data:JSON.parse(JSON.stringify(definition.data)),
    setData(values,callback){Object.assign(this.data,values);if(callback)callback()}}
}
const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve()}
const detail=(code='000001.SZ',day='20261112')=>({
  ts_code:code,symbol:code.split('.')[0],name:'测试',tradeDate:day,currentPrice:'10',change:'+1.00%',
  simulationApplicability:{applicable:true},futureTradeDates:[day==='20261112'?'20261113':'20261116','20261116'],
  simulationBase:{stock:[1,2],index:[0,0],dates:['20261111',day]},
  stockState:{marketDataState:'complete'},dataQuality:{ruleFacts:{status:'complete'},isComplete:true},
  warnings:[],monitoringFacts:[],nextDayForecast:{available:true,baseTradeDate:day,targetTradeDate:'20261113',rules:[]}
})
async function run(){
  const markup=name=>fs.readFileSync(path.join(__dirname,name,'index.wxml'),'utf8')
  const stockMarkup=markup('stock')
  assert(stockMarkup.indexOf('placement="home_history_below"')>stockMarkup.indexOf('class="history-row"'))
  assert(stockMarkup.indexOf('placement="stock_simulation_above"')<stockMarkup.indexOf('class="section simulation-section"'))
  assert.strictEqual((stockMarkup.match(/<official-ad /g)||[]).length,2)
  for(const name of ['prediction','monitor']){
    const text=markup(name)
    assert.strictEqual((text.match(/<official-ad /g)||[]).length,1)
    assert(text.indexOf('<official-ad ')<text.indexOf('class="legal-footer-links"'))
  }
  assert(!markup('privacy').includes('<official-ad'))
  assert(!markup('rules').includes('<official-ad'))
  client.policy=async()=>({mode:'rewarded',ready:true,unitId:'adunit-test'})
  api.getStockDetail=async()=>detail()
  let stock=page('./stock/index')
  stock.onLoad({})
  stock.detailRequestId=1
  stock.renderStockDetail(detail(),{items:[]},{ts_code:'000001.SZ'},1)
  stock.data.inputRows[0].stock='5'
  let p=stock.expandSimulation();await tick()
  assert.strictEqual(advertisements.length,1)
  advertisements[0].close({isEnded:true});await p
  assert.strictEqual(stock.data.visibleDays,10)
  assert.strictEqual(stock.data.inputRows[0].stock,'5')
  stock.renderStockDetail({...detail(),currentPrice:'11'},{items:[]},{ts_code:'000001.SZ'},1)
  assert.strictEqual(stock.data.visibleDays,10,'same-day quote refresh preserves unlocked layout')
  assert.strictEqual(stock.data.inputRows[0].stock,'5','same-day quote refresh preserves input')
  api.getStockDetail=async()=>detail()
  await stock.reloadStockS1ForBoundary()
  assert.strictEqual(stock.data.visibleDays,10,'same-day S1 phase change preserves unlocked layout')
  assert.strictEqual(stock.data.inputRows[0].stock,'5','same-day S1 phase change preserves input')
  stock.clearInputs()
  assert.strictEqual(stock.data.visibleDays,10)
  assert.strictEqual(stock.data.inputRows[0].stock,'')
  await stock.expandSimulation()
  assert.strictEqual(advertisements.length,1,'expanded mode does not request another video')
  stock.resetSimulationInputs()
  assert.strictEqual(stock.data.visibleDays,2)
  p=stock.expandSimulation();await tick()
  assert.strictEqual(advertisements.length,2)
  advertisements[1].close({isEnded:false});await p
  assert.strictEqual(stock.data.visibleDays,2)
  stock.data.detail.simulationApplicability={applicable:false,message:'暂不适用'}
  await stock.expandSimulation()
  assert.strictEqual(advertisements.length,2,'IPO must not show rewarded video')
  stock.data.detail.simulationApplicability={applicable:true}
  p=stock.expandSimulation();await tick()
  stock.resetSimulationInputs()
  advertisements[2].close({isEnded:true});await p
  assert.strictEqual(stock.data.visibleDays,2,'old completion cannot expand reset session')
  stock.renderStockDetail(detail('000001.SZ','20261113'),{items:[]},{ts_code:'000001.SZ'},stock.detailRequestId)
  assert.strictEqual(stock.data.visibleDays,2,'new baseline resets')
  stock.onUnload()

  let reads=0,quotes=0,statusCalls=0,available=true,target='20261113',fail=false
  api.getPredictions=async scope=>{
    reads++;if(fail&&scope==='next_day')throw Error('read failed')
    return {items:[],nextDayAvailable:true,targetTradeDate:scope==='next_day'?target:'20261112'}
  }
  api.refreshPredictions=async()=>{quotes++;return {items:[]}}
  api.getNextPredictionStatus=async()=>{statusCalls++;return {available,targetTradeDate:target,reason:'等待当日初态数据'}}
  const prediction=page('./prediction/index')
  await prediction.onLoad()
  let count=advertisements.length
  available=false
  await prediction.switchScope({currentTarget:{dataset:{scope:'next_day'}}})
  assert.strictEqual(advertisements.length,count,'not ready cannot show video')
  assert.strictEqual(prediction.data.scope,'today')
  available=true;fail=true
  p=prediction.switchScope({currentTarget:{dataset:{scope:'next_day'}}});await tick()
  advertisements[count].close({isEnded:true});await p
  assert.strictEqual(prediction.data.scope,'next_day')
  assert.strictEqual(prediction.data.nextUnlocked,true)
  assert.match(prediction.data.error,/不可用/)
  fail=false
  await prediction.loadData()
  assert.strictEqual(prediction.data.error,'','retry read after reward needs no second video')
  await prediction.switchScope({currentTarget:{dataset:{scope:'today'}}})
  await prediction.switchScope({currentTarget:{dataset:{scope:'next_day'}}})
  assert.strictEqual(advertisements.length,count+1,'same target and page grant reused')
  assert.strictEqual(quotes,0,'tab/award does not request S2')
  await prediction.switchScope({currentTarget:{dataset:{scope:'today'}}})
  target='20261116'
  p=prediction.switchScope({currentTarget:{dataset:{scope:'next_day'}}});await tick()
  assert.strictEqual(advertisements.length,count+2)
  target='20261117'
  advertisements[count+1].close({isEnded:true});await p
  assert.strictEqual(prediction.data.scope,'today','target changed while viewing cannot enter old pool')
  assert.strictEqual(prediction.data.nextUnlocked,false)
  prediction.onUnload()
  assert(statusCalls>=6)

  // The unqualified/default configuration must retain the original free entry paths.
  client.policy=async()=>({mode:'free',ready:true})
  const beforeFree=advertisements.length, beforeStatus=statusCalls
  stock=page('./stock/index')
  stock.onLoad({});stock.detailRequestId=1
  stock.renderStockDetail(detail(),{items:[]},{ts_code:'000001.SZ'},1)
  stock.data.inputRows[0].stock='3'
  await stock.expandSimulation()
  assert.strictEqual(stock.data.visibleDays,10)
  assert.strictEqual(stock.data.inputRows[0].stock,'3')
  stock.onUnload()
  const freePrediction=page('./prediction/index')
  await freePrediction.onLoad()
  await freePrediction.switchScope({currentTarget:{dataset:{scope:'next_day'}}})
  assert.strictEqual(freePrediction.data.scope,'next_day')
  assert.strictEqual(advertisements.length,beforeFree,'free mode never creates video')
  assert.strictEqual(statusCalls,beforeStatus,'free mode keeps the legacy list read path')
  freePrediction.onUnload()
  console.log('ad page integration tests passed: input/refresh/reset, IPO, next readiness/target, retry, S2 isolation, free defaults')
}
run().catch(e=>{console.error(e);process.exitCode=1})
