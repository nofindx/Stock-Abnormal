const assert = require('assert')

let stockPage
global.Page = definition => { stockPage = definition }
const { additionalRiseToThreshold, bestDeviation, buildMatrixGroups, buildNextDayForecast, calculateSimulationRows, createInputRows, nearestUpwardThreshold, presentWarnings, summaryStatus, formatMonthDay, monitoringRuleLabel, formatMonitoringSummary, remainingMonitoringDays, findStockMonitoring, selectStockMonitoring, currentSevereRuleTexts, resolveStatusBanner } = require('./index')

assert.deepStrictEqual(resolveStatusBanner({ summaryStatus: '暂无异动风险', summaryTone: 'safe' }, { dateText: '10/26-11/06' }), {
  summaryStatus: '暂无异动风险', summaryTone: 'safe', statusClass: 'safe'
})
assert.deepStrictEqual(resolveStatusBanner({ summaryStatus: '暂无异动风险', summaryTone: 'safe' }, { dateText: '10/26-11/06', badgeText: '已被重点监管' }), {
  summaryStatus: '暂无异动风险', summaryTone: 'safe', statusClass: 'monitoring'
})
assert.deepStrictEqual(resolveStatusBanner({ currentTriggered: true, summaryStatus: '当日已触发严重异动', summaryTone: 'risk' }, { dateText: '10/26-11/06' }), {
  summaryStatus: '当日已触发严重异动', summaryTone: 'risk', statusClass: 'current-triggered'
})

const forecastBase = { baseTradeDate: '20261016', targetTradeDate: '20261019', available: true }
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [] }), {
  line: '按 20261016 收盘数据预测 20261019',
  text: '未发现下一交易日可触发的异动规则',
  summaryStatus: '暂无异动风险',
  summaryTone: 'safe'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'ordinary_3d', direction: 'up', rule: '连续3个交易日内收盘价涨幅偏离值累计达到 +20%' },
  { ruleKey: 'same_direction_10d', direction: 'down', rule: '10个交易日内出现4次下跌同向普通异动' },
  { ruleKey: 'severe_10d', direction: 'up', rule: '连续10个交易日内收盘价涨幅偏离值累计达到 +100%' },
  { ruleKey: 'severe_30d', direction: 'up', rule: '连续30个交易日内收盘价涨幅偏离值累计达到 +200%' }
] }), {
  line: '按 20261016 收盘数据预测 20261019',
  text: '次日可能触发：10个交易日内出现4次下跌同向普通异动；连续10个交易日内收盘价涨幅偏离值累计达到 +100%；连续30个交易日内收盘价涨幅偏离值累计达到 +200%',
  summaryStatus: '存在异动风险',
  summaryTone: 'risk'
})
assert.deepStrictEqual(currentSevereRuleTexts([
  { title: '10 日偏离', upTriggered: true, upThreshold: 100, downTriggered: false },
  { title: '30 日偏离', upTriggered: false, downTriggered: true, downThreshold: -70 }
]), [
  '连续10个交易日内收盘价涨幅偏离值累计达到 +100%',
  '连续30个交易日内收盘价跌幅偏离值累计达到 -70%'
])
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [] }, false, [
  '连续10个交易日内收盘价涨幅偏离值累计达到 +100%'
], '20261016'), {
  line: '按 20261016 收盘数据预测',
  text: '已触发：连续10个交易日内收盘价涨幅偏离值累计达到 +100%',
  notice: '将进入重点监控池，具体以官方公告为准。',
  futureLine: '次日预测（按 20261016 收盘数据预测 20261019）',
  futureText: '未发现下一交易日可触发的异动规则',
  summaryStatus: '当日已触发严重异动',
  summaryTone: 'risk',
  currentTriggered: true
})
const currentTriggerWithThirtyDayCandidate = buildNextDayForecast({
  ...forecastBase,
  rules: [{ ruleKey: 'severe_30d', rule: '连续30个交易日偏离达到 +200%' }]
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'], '20261016')
assert.strictEqual(currentTriggerWithThirtyDayCandidate.text, '已触发：连续10个交易日内收盘价涨幅偏离值累计达到 +100%')
assert.strictEqual(currentTriggerWithThirtyDayCandidate.futureText, '次日可能触发：连续30个交易日偏离达到 +200%')
const predictionTargetIsCurrentSession = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true, rules: []
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'], '20261026')
assert.strictEqual(predictionTargetIsCurrentSession.summaryStatus, '暂无异动风险')
assert.strictEqual(predictionTargetIsCurrentSession.summaryTone, 'safe')
assert.strictEqual(predictionTargetIsCurrentSession.currentTriggered, undefined)
const predictionTargetIsCurrentRisk = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true,
  rules: [{ ruleKey: 'severe_30d', rule: '连续30个交易日内收盘价涨幅偏离值累计达到 +200%' }]
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'], '20261026')
assert.strictEqual(predictionTargetIsCurrentRisk.summaryStatus, '存在异动风险')
assert.strictEqual(predictionTargetIsCurrentRisk.text, '次日可能触发：连续30个交易日内收盘价涨幅偏离值累计达到 +200%')
const stalePredictionTarget = buildNextDayForecast({
  baseTradeDate: '20261022', targetTradeDate: '20261023', available: true, rules: []
}, false, [], '20261026')
assert.deepStrictEqual(stalePredictionTarget, {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
const currentTargetMissingDate = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '', available: true, rules: []
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'], '20261026')
assert.deepStrictEqual(currentTargetMissingDate, {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
const currentTargetMissingCurrentDate = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true, rules: []
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'])
assert.deepStrictEqual(currentTargetMissingCurrentDate, {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.strictEqual(currentTargetMissingCurrentDate.currentTriggered, undefined)
const predictionWithoutCurrentDateOrTrigger = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true, rules: []
})
assert.strictEqual(predictionWithoutCurrentDateOrTrigger.summaryStatus, '暂无异动风险')
const targetDoesNotFollowBaseDate = buildNextDayForecast({
  baseTradeDate: '20261026', targetTradeDate: '20261026', available: true, rules: []
}, false, [], '20261026')
assert.deepStrictEqual(targetDoesNotFollowBaseDate, {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
const resetTargetForecast = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true, rules: []
}, false, ['连续10个交易日内收盘价涨幅偏离值累计达到 +100%'], '20261026')
assert.strictEqual(resetTargetForecast.summaryStatus, '暂无异动风险')
assert.strictEqual(resolveStatusBanner(resetTargetForecast, { badgeText: '已被重点监管' }).statusClass, 'monitoring')
const pendingAnnouncementFacts = buildNextDayForecast({
  baseTradeDate: '20261023', targetTradeDate: '20261026', available: true,
  rules: [{ ruleKey: 'severe_30d', rule: '连续30个交易日内收盘价涨幅偏离值累计达到 +200%' }]
}, false, [], '20261026', 'pending_verification')
assert.deepStrictEqual(pendingAnnouncementFacts, {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, available: false, rules: [] }), {
  line: '', text: '次日预测暂不可用',
  summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [] }, false, [], '', 'available', false), {
  line: '', text: '次日预测暂不可用',
  summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, available: false, unavailableReason: '股票与所属指数行情基准未对齐', rules: [{ ruleKey: 'severe_30d', rule: '连续30个交易日偏离达到阈值' }] }), {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [{ ruleKey: 'severe_10d' }] }), {
  line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown'
})
assert.deepStrictEqual(buildNextDayForecast({}), { line: '', text: '次日预测暂不可用', summaryStatus: '未知状态', summaryTone: 'unknown' })
assert.deepStrictEqual(summaryStatus({ nextDayForecast: { available: false, rules: [] } }), { text: '未知状态', tone: 'unknown' })
assert.deepStrictEqual(summaryStatus({
  stockState: { dataAvailable: false },
  dataQuality: { isComplete: false, ruleFacts: { status: 'available' } },
  nextDayForecast: { ...forecastBase, rules: [] }
}), { text: '未知状态', tone: 'unknown' })
const safeForecast = { ...forecastBase, available: true, rules: [] }
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoring: { monitorType: '严重异动' }, nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoringStatus: 'active', nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '触及', monitoring: null, nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', nextDayForecast: { ...forecastBase, rules: [{ ruleKey: 'severe_30d', rule: '连续30个交易日偏离达到阈值' }] } }), { text: '存在异动风险', tone: 'risk' })
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoringStatus: 'active', nextDayForecast: { ...forecastBase, rules: [] } }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', nextDayForecast: { ...forecastBase, rules: [] } }), { text: '暂无异动风险', tone: 'safe' })
const alignedWarning = buildNextDayForecast({ ...forecastBase, rules: [{ ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' }] })
assert.strictEqual(alignedWarning.summaryTone, 'risk')
assert.strictEqual(alignedWarning.summaryStatus, '存在异动风险')
assert.strictEqual(alignedWarning.text, '次日可能触发：10个交易日内出现3次上涨同向普通异动')
const monitoredWarning = buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'ordinary_3d', rule: '连续3个交易日内收盘价涨幅偏离值累计达到 +20%' },
  { ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' }
] }, true)
assert.strictEqual(monitoredWarning.summaryTone, 'risk')
assert.strictEqual(monitoredWarning.text, '次日可能触发：10个交易日内出现3次上涨同向普通异动')
const monitoredWithSevere = buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'ordinary_3d', rule: '连续3个交易日内收盘价涨幅偏离值累计达到 +20%' },
  { ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' },
  { ruleKey: 'severe_30d', rule: '连续30个交易日内收盘价涨幅偏离值累计达到 +200%' }
] }, true)
assert.strictEqual(monitoredWithSevere.summaryTone, 'risk')
assert.strictEqual(monitoredWithSevere.text, '次日可能触发：10个交易日内出现3次上涨同向普通异动；连续30个交易日内收盘价涨幅偏离值累计达到 +200%')
const monitoredAndSafe = buildNextDayForecast({ ...forecastBase, rules: [] }, true)
assert.strictEqual(monitoredAndSafe.summaryTone, 'safe')
assert.strictEqual(monitoredAndSafe.text, '未发现下一交易日可触发的异动规则')
const allRuleKeys = ['ordinary_3d', 'same_direction_10d', 'severe_10d', 'severe_30d']
allRuleKeys.forEach(ruleKey => {
  const expected = ruleKey === 'ordinary_3d'
    ? { text: '存在警告风险', tone: 'warning' }
    : { text: '存在异动风险', tone: 'risk' }
  assert.deepStrictEqual(summaryStatus({
    monitoring: { active: true },
    nextDayForecast: { ...forecastBase, rules: [{ ruleKey, rule: '对应规则描述' }] }
  }), expected)
})
assert.deepStrictEqual(summaryStatus({
  monitoring: { active: true },
  warnings: allRuleKeys.map(title => ({ title, tone: 'safe' })),
  nextDayForecast: { ...forecastBase, rules: [] }
}), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ nextDayForecast: { ...forecastBase, rules: [{ ruleKey: 'ordinary_3d', rule: '连续3个交易日偏离达到阈值' }] } }), { text: '存在警告风险', tone: 'warning' })
assert.deepStrictEqual(buildNextDayForecast(
  { baseTradeDate: '20261026', targetTradeDate: '20261027', available: true, rules: [] }, true, [], '20261026'
), {
  line: '按 20261026 收盘数据预测 20261027',
  text: '未发现下一交易日可触发的异动规则',
  summaryStatus: '暂无异动风险',
  summaryTone: 'safe'
})
assert.strictEqual(formatMonthDay('2026-10-19'), '20261019')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-30d', monitorType: '30日严重异动', symbol: '600825' }), '连续30个交易日内收盘价涨幅偏离值累计达到 +200%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-10d', monitorType: '10日严重异动', symbol: '920229' }), '连续10个交易日内收盘价涨幅偏离值累计达到 +150%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-10d', eventDirection: 'down', symbol: '600825' }), '连续10个交易日内收盘价跌幅偏离值累计达到 -50%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'ordinary' }), '交易所将对该证券的异常交易行为进行进一步从严认定')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'delisting-warning' }), '股票进入退市整理期')
assert.strictEqual(formatMonitoringSummary({ active: true, riskTone: 'delisting-warning', monitorStartDate: '2026-09-30', monitorEndDate: '2026-10-27' }, '20261026').badgeText, '退市整理期')
assert.strictEqual(formatMonitoringSummary({ active: true, monitorType: '退市倒计时', monitorStartDate: '2026-09-30', monitorEndDate: '2026-10-27' }, '20261026').badgeText, '退市整理期')
assert.strictEqual(formatMonitoringSummary({ active: true, riskTone: 'severe-10d', monitorStartDate: '2026-10-23', monitorEndDate: '2026-11-05' }, '20261026').badgeText, '已被重点监管')
assert.deepStrictEqual(formatMonitoringSummary({ active: true, monitorStartDate: '2026-10-15', monitorEndDate: '2026-10-22', monitorDates: ['2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'], riskTone: 'severe-10d', days: 4 }, '20261018'), {
  dateText: '20261015-20261022', badgeText: '已被重点监管', ruleText: '连续10个交易日内收盘价涨幅偏离值累计达到 +100%', remainingText: '4'
})
assert.strictEqual(remainingMonitoringDays({ monitorDates: ['2026-10-18', '2026-10-19', '2026-10-20'] }, '20261018'), '2')
assert.strictEqual(remainingMonitoringDays({ monitorDates: ['2026-10-20', '2026-10-21'], monitorEndDate: '2026-10-21' }, '20261017'), '4')
assert.strictEqual(remainingMonitoringDays({ monitorEndDate: '2026-10-18' }, '20261018'), '0')
assert.strictEqual(formatMonitoringSummary(null), null)
assert.strictEqual(findStockMonitoring([
  { ts_code: '301190.SZ', riskTone: 'ordinary', monitorEndDate: '2026-10-22' },
  { ts_code: '301190.SZ', riskTone: 'severe-10d', monitorEndDate: '2026-10-22' },
  { ts_code: '688137.SH', riskTone: 'severe-30d' }
], '301190.SZ').riskTone, 'severe-10d')
assert.strictEqual(findStockMonitoring([
  { ts_code: '301313.SZ', riskTone: 'ordinary', monitorStartDate: '2026-10-26', monitorEndDate: '2026-11-06' },
  { ts_code: '301313.SZ', riskTone: 'severe-10d', monitorStartDate: '2026-10-27', monitorEndDate: '2026-11-09' }
], '301313.SZ', '20261026').riskTone, 'ordinary')
assert.strictEqual(findStockMonitoring([
  { ts_code: '301313.SZ', riskTone: 'severe-10d', monitorStartDate: '2026-10-27', monitorEndDate: '2026-11-09' }
], '301313.SZ', '20261026'), null)
assert.strictEqual(findStockMonitoring([
  { ts_code: '301313.SZ', riskTone: 'severe-10d', monitorStart: '10-27', monitorEnd: '11-09' }
], '301313.SZ', '20261026'), null)
const delistingAndSevereFacts = findStockMonitoring([
  { ts_code: '002731.SZ', riskTone: 'severe-10d', monitorEndDate: '2026-11-05' },
  { ts_code: '002731.SZ', riskTone: 'delisting-warning', monitorType: '退市倒计时', monitorEndDate: '2026-10-27' }
], '002731.SZ')
assert.strictEqual(delistingAndSevereFacts.riskTone, 'delisting-warning')
assert.strictEqual(formatMonitoringSummary(delistingAndSevereFacts, '20261026').badgeText, '退市整理期')
const yuandaoDelistingFacts = findStockMonitoring([
  { ts_code: '301139.SZ', riskTone: 'severe-10d', monitorEndDate: '2026-11-05' },
  { ts_code: '301139.SZ', riskTone: 'delisting-warning', monitorType: '退市整理风险警示', monitorEndDate: '2026-10-27' }
], '301139.SZ')
assert.strictEqual(formatMonitoringSummary(yuandaoDelistingFacts, '20261026').badgeText, '退市整理期')
const selectedOverlappingMonitoring = selectStockMonitoring(
  { riskTone: 'severe-10d', monitorEndDate: '2026-11-05' },
  [
    { symbol: '002731', riskTone: 'delisting-warning', monitorType: '退市倒计时', monitorEndDate: '2026-10-27' },
    { symbol: '600000', riskTone: 'delisting-warning', monitorType: '退市倒计时' }
  ],
  '002731.SZ',
  '萃华退'
)
assert.strictEqual(selectedOverlappingMonitoring.riskTone, 'delisting-warning')
assert.strictEqual(formatMonitoringSummary(selectedOverlappingMonitoring, '20261026').badgeText, '退市整理期')
const inactiveDelistingFact = findStockMonitoring([
  { ts_code: '002731.SZ', riskTone: 'delisting-warning', active: false },
  { ts_code: '002731.SZ', riskTone: 'severe-10d', active: true }
], '002731.SZ')
assert.strictEqual(inactiveDelistingFact.riskTone, 'severe-10d')

const maxTrailingInterval = bestDeviation([10, 10, 0, 0, 0, 0, 0, 0, -50, 10], Array(10).fill(0), 10)
assert.strictEqual(maxTrailingInterval.window, 1)
assert(Math.abs(maxTrailingInterval.value - 10) < 1e-9)

const detail = {
  simulationBase: {
    stock: [-1.595428027146073, -11.288566243194207, -6.205673758865249, 5.40933546604625, 0.3172851427783119, -12.307480748074806, -2.540379488787836, -0.933226065969428, 15.332142277082994, 0.9012815096465232, -2.763433356594558, -8.138366585330846, 19.999999999999996, 0.390625, 8.949416342412464, 18.75, -0.7218045112781901, 20.00403918004645, 4.76270615954224, -1.3333333333333308, 1.9619016606968296, 2.259481037924171, 10.360712054965626, 13.47010965688007, -6.47795997256686, 7.453333333333334, -8.81623030152625, -6.674831598285369, 4.403616214639827, 5.572625698324041],
    index: [3.7658607124912535, -1.8500547629862083, 1.3379062879610215, -2.1878816113135025, -1.8171511875105129, -0.3975960138890122, -2.098146111894672, 2.419858845212697, -1.5187023705216407, -0.6874061358065564, -0.6860883911408822, -1.0084129791510188, -1.6176127885134115, 1.5502535847651044, 4.138481573697117, -0.6124729734010681, 2.8851695173508496, 0.29368927485928786, 0.4559439043958502, -0.2515011935436129, -2.347117525427578, -4.062688054297759, 0.8584941044244676, -2.5063236839167446, -4.815785980405163, 0.06472461242996719, -2.096040772226626, 0.03195437708378712, -3.287593869718375, -1.5026154492631782]
  },
  simulationThresholds: { '10': { up: 100 }, '30': { up: 200 } }
}

const rows = createInputRows(2, ['2026-10-16', '2026-10-19'])
assert.strictEqual(rows[0].stock, '')
assert.strictEqual(rows[0].index, '')
assert.deepStrictEqual(buildMatrixGroups(rows)[0].rows.map(item => item.label), ['股票涨幅', '指数涨幅', '10日偏离', '30日偏离', '距最近阈值'])
// Object identity is not a valid WXML list key. Stable per-column IDs must
// survive edits, expansion and clearing, including placeholder cells.
const simulationMarkup = require('fs').readFileSync(require('path').join(__dirname, 'index.wxml'), 'utf8')
assert(simulationMarkup.includes('wx:for="{{matrix.columns}}" wx:for-item="column" wx:key="rowIndex"'))
assert(simulationMarkup.includes('wx:for="{{matrixRow.values}}" wx:for-item="cell" wx:key="rowIndex"'))
assert(simulationMarkup.includes('wx:if="{{!simulationReady}}" class="simulation-loading"'))
assert(simulationMarkup.includes('<block wx:if="{{simulationReady}}">'))
assert(simulationMarkup.includes('<text class="matrix-suffix">%</text>'), 'blank simulation inputs must still show their percent unit')
assert(!simulationMarkup.includes('（百分点）'))
assert(simulationMarkup.includes('距最近阈值为在该模拟价格上还能上涨的百分比，指数按该行假设不变'))
for (const count of [2, 10]) {
  const initial = buildMatrixGroups(createInputRows(count))
  const changed = buildMatrixGroups(createInputRows(count).map((row, i) => ({ ...row, stock: String(i + 1) })))
  initial.forEach((group, index) => {
    const ids = group.columns.map(column => column.rowIndex)
    assert.strictEqual(new Set(ids).size, ids.length)
    assert.deepStrictEqual(changed[index].columns.map(column => column.rowIndex), ids)
    group.rows.forEach(row => assert.deepStrictEqual(row.values.map(cell => cell.rowIndex), ids))
  })
}
rows[0].stock = '16'
rows[0].index = '0'
const result = calculateSimulationRows(rows, detail)
assert.strictEqual(result[0].deviation10, '+56.19%')
assert.strictEqual(result[0].deviation30, '+200.68%')
assert.strictEqual(result[0].space, '已触发')
assert.strictEqual(result[0].deviation10Window, 10)
assert.strictEqual(result[0].deviation30Window, 23)
assert.notStrictEqual(result[1].deviation10, '--')
assert.strictEqual(result[1].stock, '')
assert.strictEqual(result[1].index, '')
assert.strictEqual(createInputRows(1, ['2026-10-19'])[0].date, '20261019')
const blankResult = calculateSimulationRows(createInputRows(1), detail)
assert.notStrictEqual(blankResult[0].deviation10, '--')

const downsideWarning = presentWarnings([{
  title: '10 日偏离', value: '--', downValue: '-55.00%',
  target: '阈值 +100% / -50%', downTriggered: true, downWindow: 10
}])[0]
assert.strictEqual(downsideWarning.currentText, '-55.00%')
assert.strictEqual(downsideWarning.currentNumber, '-55.00')
assert.strictEqual(downsideWarning.currentUnit, '%')
assert.strictEqual(downsideWarning.currentFontSize, 27)
assert.strictEqual(downsideWarning.target, '阈值 -50%')
assert.strictEqual(downsideWarning.tone, 'triggered')
const pendingFactWarnings = presentWarnings([
  { title: '3 日偏离', value: '+5.04%', downValue: '-4.04%', target: '阈值 +20% / -20%' },
  { title: '10 日同向', up: '0', down: '0', target: '阈值 4 次' }
], { forceUnknown: true })
assert.deepStrictEqual(pendingFactWarnings.map(item => item.tone), ['unknown', 'unknown'])
assert.deepStrictEqual(pendingFactWarnings.map(item => item.labelTone), ['unknown', 'unknown'])
assert.deepStrictEqual(pendingFactWarnings.map(item => item.ringText), ['待核验', '待核验'])
assert(pendingFactWarnings.every(item => item.target.includes('待核验')))
const orderedWarnings = presentWarnings([
  { title: '30 日偏离', value: '+20.00%', downValue: '-5.00%', target: '阈值 +200% / -70%' },
  { title: '10 日偏离', value: '+30.00%', downValue: '-5.00%', target: '阈值 +100% / -50%' },
  { title: '10 日同向', up: '1', down: '0', target: '阈值 4 次' },
  { title: '3 日偏离', value: '+10.00%', downValue: '-3.00%', target: '阈值 +20% / -20%' }
], { nextDayRules: [
  { ruleKey: 'ordinary_3d' },
  { ruleKey: 'same_direction_10d' },
  { ruleKey: 'severe_10d' },
  { ruleKey: 'severe_30d' }
] })
assert.deepStrictEqual(orderedWarnings.map(item => item.title), ['3 日偏离', '10 日同向', '10 日偏离', '30 日偏离'])
assert.deepStrictEqual(orderedWarnings.map(item => item.labelTone), ['warning', 'severe', 'severe', 'severe'])
assert.strictEqual(orderedWarnings[1].currentText, '+1次')
assert.strictEqual(orderedWarnings[1].currentNumber, '+1')
assert.strictEqual(orderedWarnings[1].currentUnit, '次')
const isolatedNextDayRisk = presentWarnings([
  { title: '30 日偏离', value: '+20.00%', downValue: '-5.00%', target: '阈值 +200% / -70%' },
  { title: '10 日偏离', value: '+30.00%', downValue: '-5.00%', target: '阈值 +100% / -50%' }
], { nextDayRules: [{ ruleKey: 'severe_30d' }] })
assert.deepStrictEqual(isolatedNextDayRisk.map(item => item.labelTone), ['base', 'severe'])

const downsideNextDayRisk = presentWarnings([{
  title: '30 日偏离', value: '+7.64%', downValue: '-65.40%',
  target: '阈值 +200% / -70%'
}], { nextDayRules: [{ ruleKey: 'severe_30d', direction: 'down' }] })[0]
assert.strictEqual(downsideNextDayRisk.currentText, '-65.40%')
assert.strictEqual(downsideNextDayRisk.currentNumber, '-65.40')
assert.strictEqual(downsideNextDayRisk.currentUnit, '%')
assert.strictEqual(downsideNextDayRisk.target, '阈值 -70%')
assert.strictEqual(downsideNextDayRisk.remainingText, '还差 4.60%')
assert.strictEqual(downsideNextDayRisk.labelTone, 'severe')

const forecastUpWithCurrentDownside = presentWarnings([{
  title: '3 日偏离', value: '--', upValue: '--', downValue: '-16.03%',
  target: '阈值 +30% / -30%', upThreshold: 30, downThreshold: -30,
  upTriggered: false, downTriggered: false
}], { nextDayRules: [{ ruleKey: 'ordinary_3d', direction: 'up' }] })[0]
assert.strictEqual(forecastUpWithCurrentDownside.currentText, '-16.03%')
assert.strictEqual(forecastUpWithCurrentDownside.target, '阈值 -30%')
assert.strictEqual(forecastUpWithCurrentDownside.labelTone, 'warning')

const mixedDirectionNextDayRisk = presentWarnings([{
  title: '30 日偏离', value: '+7.64%', downValue: '-65.40%',
  target: '阈值 +200% / -70%'
}], { nextDayRules: [
  { ruleKey: 'severe_30d', direction: 'up' },
  { ruleKey: 'severe_30d', direction: 'down' }
] })[0]
assert.strictEqual(mixedDirectionNextDayRisk.currentText, '-65.40%')
assert.strictEqual(mixedDirectionNextDayRisk.target, '阈值 -70%')

const forecastDirectionDoesNotChangeCurrentMetric = presentWarnings([{
  title: '30 日偏离', value: '+160.00%', downValue: '-65.40%',
  target: '阈值 +200% / -70%'
}], { nextDayRules: [{ ruleKey: 'severe_30d', direction: 'up' }] })[0]
assert.strictEqual(forecastDirectionDoesNotChangeCurrentMetric.currentText, '-65.40%')
assert.strictEqual(forecastDirectionDoesNotChangeCurrentMetric.target, '阈值 -70%')

const sameDirectionNextDayRisk = presentWarnings([{
  title: '10 日同向', up: 2, down: 0, target: '阈值 +3次'
}], { nextDayRules: [{ ruleKey: 'same_direction_10d' }] })[0]
assert.strictEqual(sameDirectionNextDayRisk.labelTone, 'severe')
assert.strictEqual(sameDirectionNextDayRisk.toneColor, '#D94A45')

const currentSameDirectionTrigger = currentSevereRuleTexts([{
  title: '10 日同向', up: 3, down: 0, target: '阈值 +3次'
}])
assert.deepStrictEqual(currentSameDirectionTrigger, ['连续10个交易日内出现3次上涨同向异常波动'])

const ordinaryOnlyDetail = {
  simulationBase: { stock: Array(29).fill(0), index: Array(29).fill(0) },
  simulationThresholds: { '3': { up: 20 }, '10': { up: 100 }, '30': { up: 200 } }
}
const ordinaryOnlyRows = createInputRows(1)
ordinaryOnlyRows[0].stock = '25'
const ordinaryOnlyResult = calculateSimulationRows(ordinaryOnlyRows, ordinaryOnlyDetail)
assert.strictEqual(ordinaryOnlyResult[0].deviation10, '+25.00%')
assert.strictEqual(ordinaryOnlyResult[0].space, '60.00%', '当前模拟价上再涨60%，累计涨幅由25%变为100%')

// 兼容接口的下跌阈值不参与模拟；距离只比较两个上涨阈值。
const upwardOnlyDetail = {
  simulationBase: { stock: Array(29).fill(0), index: Array(29).fill(0) },
  simulationThresholds: { '10': { up: 100, down: -50 }, '30': { up: 200, down: -70 } }
}
assert.strictEqual(calculateSimulationRows(createInputRows(1), upwardOnlyDetail)[0].space, '100.00%')
const decliningRows = createInputRows(1)
decliningRows[0].stock = '-60'
const decliningResult = calculateSimulationRows(decliningRows, upwardOnlyDetail)[0]
assert.strictEqual(decliningResult.deviation10, '-60.00%')
assert.strictEqual(decliningResult.space, '400.00%', '当前价格已跌60%，再涨400%才到累计+100%；不因下跌阈值标记触发')
const allNegativeInterval = bestDeviation([-30, -20], [0, 0], 10)
assert.strictEqual(allNegativeInterval.window, 1)
assert(Math.abs(allNegativeInterval.value + 20) < 1e-9, '全为负值时仍取最大偏离，不切换为下跌指标')

const rollingDetail = {
  ...upwardOnlyDetail,
  simulationBase: { stock: Array(20).fill(0).concat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), index: Array(30).fill(0) }
}
const rollingResults = calculateSimulationRows(createInputRows(10), rollingDetail)
assert.deepStrictEqual(rollingResults.slice(5).map(row => row.space), [
  [7, 8, 9, 10], [8, 9, 10], [9, 10], [10], []
].map(returns => `${(Math.min(
  2 / returns.reduce((factor, value) => factor * (1 + value / 100), 1),
  3 / Array.from({ length: 10 }, (_, i) => i + 1).reduce((factor, value) => factor * (1 + value / 100), 1)
) * 100 - 100).toFixed(2)}%`))
assert.strictEqual(rollingResults[9].deviation10, '+0.00%')

const thirtyDayIsCloser = {
  ...upwardOnlyDetail,
  simulationBase: { stockByWindow: { '10': [0], '30': [160] }, indexByWindow: { '10': [0], '30': [0] } }
}
assert.strictEqual(calculateSimulationRows(createInputRows(1), thirtyDayIsCloser)[0].space, '15.38%', '当前累计价格为2.6倍，涨至3倍还需15.38%，不是40个百分点')
for (const [stockReturn, expected] of [[99, '0.50%'], [100, '已触发'], [101, '已触发']]) {
  const boundaryRows = createInputRows(1)
  boundaryRows[0].stock = String(stockReturn)
  assert.strictEqual(calculateSimulationRows(boundaryRows, upwardOnlyDetail)[0].space, expected)
}
assert.strictEqual(calculateSimulationRows(createInputRows(1), {
  ...upwardOnlyDetail, simulationThresholds: { '10': { up: null, down: -50 }, '30': { down: -70 } }
})[0].space, '--', '没有有效上涨阈值时不能用下跌阈值或零值代替')
assert.strictEqual(calculateSimulationRows(createInputRows(1), {
  simulationBase: upwardOnlyDetail.simulationBase
})[0].space, '--', '缺少整个阈值对象时不能猜测主板上涨阈值')
const invalidRows = createInputRows(2)
invalidRows[0].stock = 'invalid'
assert.deepStrictEqual(calculateSimulationRows(invalidRows, upwardOnlyDetail).map(row => row.space), ['--', '--'])
assert.strictEqual(calculateSimulationRows(createInputRows(1), {
  ...upwardOnlyDetail, simulationThresholds: { '10': { up: 150, down: -60 }, '30': { up: 300, down: -75 } }
})[0].space, '150.00%', '使用响应给出的板块上涨阈值，不硬编码主板或下跌阈值')

for (const [metrics, expected] of [
  [[{ deviation: 50, up: 100, requiredRise: 50 / 1.5 }, { deviation: 150, up: 200, requiredRise: 50 / 2.5 }], '20.00%'],
  [[{ deviation: -20, up: 100, requiredRise: 120 / 0.8 }, { deviation: 190, up: 200, requiredRise: 10 / 2.9 }], '3.45%'],
  [[{ deviation: 99.999, up: 100, requiredRise: 0.001 / 1.99999 }, { deviation: 0, up: 200, requiredRise: 200 }], '<0.01%'],
  [[{ deviation: null, up: 100 }, { deviation: 150, up: 200 }], '--'],
  [[{ deviation: null, up: 100 }, { deviation: 200, up: 200 }], '已触发'],
  [[{ deviation: 50, up: 100 }, { deviation: 150, up: 200, requiredRise: 20 }], '--'],
  [[{ deviation: 0, up: 100, resetEffective: true }, { deviation: 0, up: 200, resetEffective: true }], '--'],
  [[{ deviation: 0, up: 100, resetEffective: true }, { deviation: 150, up: 200, requiredRise: 20 }], '20.00%'],
  [[{ deviation: 50, up: null }, { deviation: 150, up: null }], '--']
]) {
  assert.strictEqual(nearestUpwardThreshold(metrics), expected)
}
assert(Math.abs(additionalRiseToThreshold([25], [10], 10, 100) - 68) < 1e-9)
assert.strictEqual(additionalRiseToThreshold([1], [0, 1], 10, 100), null)
assert.strictEqual(additionalRiseToThreshold([-100], [0], 10, 100), null)
const differentClosestWindow = { stock: [3, 5], index: [3.2, 0] }
assert.strictEqual(bestDeviation(differentClosestWindow.stock, differentClosestWindow.index, 10).window, 1)
const twoDayRequiredRise = ((2 + 0.032) / (1.03 * 1.05) - 1) * 100
assert(Math.abs(additionalRiseToThreshold(differentClosestWindow.stock, differentClosestWindow.index, 10, 100) - twoDayRequiredRise) < 1e-9,
  '最大偏离在1日窗口，最先触及价格边界却在2日窗口，不能只反算最大偏离窗口')
for (const simulationBase of [
  undefined,
  { stock: [], index: [] },
  { stock: [1, 2], index: [0] },
  { stock: [null], index: [0] },
  { stock: [NaN], index: [0] },
  { stock: [0], index: [Infinity] },
  { stockByWindow: { '10': [0], '30': [150] }, indexByWindow: { '30': [0] } }
]) {
  assert.strictEqual(calculateSimulationRows(createInputRows(1), {
    ...upwardOnlyDetail, simulationBase
  })[0].space, '--', '缺失/错位/非有限的历史不能伪装成最近阈值')
}
assert.strictEqual(calculateSimulationRows(createInputRows(1), {
  ...upwardOnlyDetail, dataQuality: { isComplete: false }
})[0].space, '--')
const futureResetDetail = {
  simulationBase: {
    stock: [80, 10], index: [0, 0],
    stockByWindow: { '10': [80, 10], '30': [80, 10] },
    indexByWindow: { '10': [0, 0], '30': [0, 0] },
    datesByWindow: { '10': ['20261029', '20261030'], '30': ['20261029', '20261030'] },
    resetDatesByWindow: { '10': ['20261102'], '30': ['20261102'] }
  },
  simulationThresholds: upwardOnlyDetail.simulationThresholds
}
const resetRows = createInputRows(2, ['20261102', '20261103'])
resetRows[0].stock = '5'
resetRows[1].stock = '2'
const resetResults = calculateSimulationRows(resetRows, futureResetDetail)
assert.deepStrictEqual(resetResults.map(row => row.deviation10), ['+0.00%', '+7.10%'])
assert.deepStrictEqual(resetResults.map(row => row.space), ['--', `${((2 / 1.071 - 1) * 100).toFixed(2)}%`])
assert.strictEqual(resetResults[0].deviation10Window, 0)
const ordinaryResetOnly = {
  ...futureResetDetail,
  simulationBase: { ...futureResetDetail.simulationBase, resetDatesByWindow: { '10': [], '30': [] } }
}
assert.strictEqual(calculateSimulationRows(resetRows, ordinaryResetOnly)[0].space, '已触发', '普通重置不清空 10/30 模拟基线')

// Independent exhaustive trailing-interval oracle for every simulated T+N.
let randomState = 20261104
const random = () => { randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0; return randomState / 4294967296 }
const compoundOracle = values => (values.reduce((factor, value) => factor * (1 + value / 100), 1) - 1) * 100
for (let sample = 0; sample < 80; sample += 1) {
  const historyStock = Array.from({ length: 30 }, () => random() * 20 - 8)
  const historyIndex = Array.from({ length: 30 }, () => random() * 6 - 3)
  const simulated = createInputRows(10).map(row => ({ ...row, stock: String(random() * 20 - 8), index: String(random() * 6 - 3) }))
  const thresholds = sample % 2 ? [100, 200] : [150, 300]
  const output = calculateSimulationRows(simulated, {
    simulationBase: { stock: historyStock, index: historyIndex },
    simulationThresholds: { '10': { up: thresholds[0] }, '30': { up: thresholds[1] } }
  })
  output.forEach((row, i) => {
    const stocks = historyStock.concat(simulated.slice(0, i + 1).map(item => Number(item.stock)))
    const indexes = historyIndex.concat(simulated.slice(0, i + 1).map(item => Number(item.index)))
    const deviations = [10, 30].map(horizon => Math.max(...Array.from({ length: horizon }, (_, n) =>
      compoundOracle(stocks.slice(-(n + 1))) - compoundOracle(indexes.slice(-(n + 1))))))
    // Independent price-factor inversion across every permitted trailing window.
    const distance = Math.min(...[10, 30].flatMap((horizon, n) => Array.from({ length: horizon }, (_, k) => {
      const stockFactor = stocks.slice(-(k + 1)).reduce((factor, value) => factor * (1 + value / 100), 1)
      const indexFactor = indexes.slice(-(k + 1)).reduce((factor, value) => factor * (1 + value / 100), 1)
      return ((indexFactor + thresholds[n] / 100) / stockFactor - 1) * 100
    })))
    const triggered = deviations.some((value, n) => value >= thresholds[n])
    const expected = triggered ? '已触发' : distance < 0.005 ? '<0.01%' : `${distance.toFixed(2)}%`
    assert.strictEqual(row.space, expected, `sample=${sample}, T+${i + 1}`)
    if (!triggered) {
      assert(row.space.endsWith('%'), '剩余可涨幅必须保留百分号')
      const maxExcessAt = extra => {
        const adjusted = stocks.slice()
        adjusted[adjusted.length - 1] = (1 + adjusted[adjusted.length - 1] / 100) * (1 + extra / 100) * 100 - 100
        return Math.max(...[10, 30].flatMap((horizon, n) => Array.from({ length: horizon }, (_, k) =>
          compoundOracle(adjusted.slice(-(k + 1))) - compoundOracle(indexes.slice(-(k + 1))) - thresholds[n])))
      }
      assert(Math.abs(maxExcessAt(distance)) < 1e-7, '反算后在当前模拟价格上涨至边界，正式偏离必须恰好触及')
      assert(maxExcessAt(distance - 0.0001) < 0, '比最近边界略低时，所有上涨阈值均未触及')
    }
  })
}

// 清空只重建当前展开范围的本地输入和计算结果，不读取行情或修改股票状态。
for (const days of [2, 10]) {
  const futureTradeDates = ['20261103', '20261104', '20261105', '20261106', '20261109', '20261110', '20261111', '20261112', '20261113', '20261116']
  const defaultRows = calculateSimulationRows(createInputRows(days, futureTradeDates), ordinaryOnlyDetail)
  const editedRows = defaultRows.map((row, index) => ({
    ...row, stock: String(index + 5), index: '-1.5', stockSelectAll: true,
    deviation10: '+999.00%', deviation30: '+999.00%', space: '已触发'
  }))
  const selected = { ts_code: '301313.SZ' }
  const page = {
    data: { simulationExpanded: days === 10, visibleDays: days, futureTradeDates, detail: ordinaryOnlyDetail, selected, inputRows: editedRows, matrixGroups: buildMatrixGroups(editedRows) },
    setData(values) { Object.assign(this.data, values) }
  }
  stockPage.clearInputs.call(page)
  assert.deepStrictEqual(page.data.inputRows, defaultRows, `${days} 日清空恢复默认计算，不残留模拟结果`)
  assert.deepStrictEqual(page.data.matrixGroups, buildMatrixGroups(defaultRows))
  assert.strictEqual(page.data.simulationExpanded, days === 10)
  assert.strictEqual(page.data.visibleDays, days)
  assert.strictEqual(page.data.detail, ordinaryOnlyDetail)
  assert.strictEqual(page.data.selected, selected)
  assert.strictEqual(page.data.futureTradeDates, futureTradeDates)
  stockPage.clearInputs.call(page)
  assert.deepStrictEqual(page.data.inputRows, defaultRows, '重复清空保持默认态')
}

console.log('stock simulation tests passed')
