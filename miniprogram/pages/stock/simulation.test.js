const assert = require('assert')

global.Page = () => {}
const { bestDeviation, buildMatrixGroups, buildNextDayForecast, calculateSimulationRows, createInputRows, presentWarnings, summaryStatus, formatMonthDay, monitoringRuleLabel, formatMonitoringSummary, remainingMonitoringDays, findStockMonitoring } = require('./index')

const forecastBase = { baseTradeDate: '20261016', targetTradeDate: '20261019', available: true }
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, rules: [] }), {
  line: '按 2026-10-16 收盘数据预测 2026-10-19',
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
  line: '按 2026-10-16 收盘数据预测 2026-10-19',
  text: '次日可能触发：连续10个交易日内收盘价涨幅偏离值累计达到 +100%；连续30个交易日内收盘价涨幅偏离值累计达到 +200%',
  summaryStatus: '存在异动风险',
  summaryTone: 'risk'
})
assert.deepStrictEqual(buildNextDayForecast({ ...forecastBase, available: false, rules: [] }), {
  line: '', text: '次日预测暂不可用',
  summaryStatus: '次日预测暂不可用', summaryTone: 'unknown'
})
assert.strictEqual(buildNextDayForecast({ ...forecastBase, available: false, unavailableReason: '股票与所属指数行情基准未对齐', rules: [] }).text, '次日预测暂不可用：股票与所属指数行情基准未对齐')
assert.strictEqual(buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'severe_10d' }
] }).text, '次日可能触发：次日预测规则说明暂不可用')
assert.strictEqual(buildNextDayForecast({}).text, '预测数据版本未同步')
assert.deepStrictEqual(summaryStatus({ nextDayForecast: { available: false, rules: [] } }), { text: '次日预测暂不可用', tone: 'unknown' })
const safeForecast = { ...forecastBase, available: true, rules: [] }
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoring: { monitorType: '严重异动' }, nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoringStatus: 'active', nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '触及', monitoring: null, nextDayForecast: safeForecast }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', nextDayForecast: { ...forecastBase, rules: [{ ruleKey: 'severe_30d' }] } }), { text: '存在异动风险', tone: 'risk' })
assert.deepStrictEqual(summaryStatus({ status: '正常', monitoringStatus: 'active', nextDayForecast: { ...forecastBase, rules: [] } }), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ status: '正常', nextDayForecast: { ...forecastBase, rules: [] } }), { text: '暂无异动风险', tone: 'safe' })
const alignedWarning = buildNextDayForecast({ ...forecastBase, rules: [{ ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' }] })
assert.strictEqual(alignedWarning.summaryTone, 'warning')
assert.strictEqual(alignedWarning.text, '次日可能触发：10个交易日内出现3次上涨同向普通异动')
const monitoredWarning = buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'ordinary_3d', rule: '连续3个交易日内收盘价涨幅偏离值累计达到 +20%' },
  { ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' }
] }, true)
assert.strictEqual(monitoredWarning.summaryTone, 'risk')
assert.strictEqual(monitoredWarning.text, '次日可能触发：连续3个交易日内收盘价涨幅偏离值累计达到 +20%；10个交易日内出现3次上涨同向普通异动')
const monitoredWithSevere = buildNextDayForecast({ ...forecastBase, rules: [
  { ruleKey: 'ordinary_3d', rule: '连续3个交易日内收盘价涨幅偏离值累计达到 +20%' },
  { ruleKey: 'same_direction_10d', rule: '10个交易日内出现3次上涨同向普通异动' },
  { ruleKey: 'severe_30d', rule: '连续30个交易日内收盘价涨幅偏离值累计达到 +200%' }
] }, true)
assert.strictEqual(monitoredWithSevere.summaryTone, 'risk')
assert.strictEqual(monitoredWithSevere.text, '次日可能触发：连续30个交易日内收盘价涨幅偏离值累计达到 +200%')
const monitoredAndSafe = buildNextDayForecast({ ...forecastBase, rules: [] }, true)
assert.strictEqual(monitoredAndSafe.summaryTone, 'safe')
assert.strictEqual(monitoredAndSafe.text, '未发现下一交易日可触发的异动规则')
const allRuleKeys = ['ordinary_3d', 'same_direction_10d', 'severe_10d', 'severe_30d']
allRuleKeys.forEach(ruleKey => {
  assert.deepStrictEqual(summaryStatus({
    monitoring: { active: true },
    nextDayForecast: { ...forecastBase, rules: [{ ruleKey }] }
  }), { text: '存在异动风险', tone: 'risk' })
})
assert.deepStrictEqual(summaryStatus({
  monitoring: { active: true },
  warnings: allRuleKeys.map(title => ({ title, tone: 'safe' })),
  nextDayForecast: { ...forecastBase, rules: [] }
}), { text: '暂无异动风险', tone: 'safe' })
assert.deepStrictEqual(summaryStatus({ nextDayForecast: { ...forecastBase, rules: [{ ruleKey: 'ordinary_3d' }] } }), { text: '存在警告风险', tone: 'warning' })
assert.strictEqual(formatMonthDay('2026-10-19'), '10/19')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-30d', monitorType: '30日严重异动', symbol: '600825' }), '连续30个交易日内收盘价涨幅偏离值累计达到 +200%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-10d', monitorType: '10日严重异动', symbol: '920229' }), '连续10个交易日内收盘价涨幅偏离值累计达到 +150%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'severe-10d', eventDirection: 'down', symbol: '600825' }), '连续10个交易日内收盘价跌幅偏离值累计达到 -50%')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'ordinary' }), '交易所将对该证券的异常交易行为进行进一步从严认定')
assert.strictEqual(monitoringRuleLabel({ riskTone: 'delisting-warning' }), '股票进入退市整理期')
assert.deepStrictEqual(formatMonitoringSummary({ active: true, monitorStartDate: '2026-10-15', monitorEndDate: '2026-10-22', monitorDates: ['2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'], riskTone: 'severe-10d', days: 4 }, '20261018'), {
  dateText: '10/15-10/22', ruleText: '连续10个交易日内收盘价涨幅偏离值累计达到 +100%', remainingText: '4'
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
assert.deepStrictEqual(buildMatrixGroups(rows)[0].rows.map(item => item.label), ['股票涨幅', '指数涨幅', '10日偏离', '30日偏离', '安全空间'])
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
assert.strictEqual(createInputRows(1, ['2026-10-19'])[0].date, '10-19')
const blankResult = calculateSimulationRows(createInputRows(1), detail)
assert.notStrictEqual(blankResult[0].deviation10, '--')

console.log('stock simulation tests passed')

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
assert.deepStrictEqual(orderedWarnings.map(item => item.labelTone), ['warning', 'warning', 'severe', 'severe'])
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

const ordinaryOnlyDetail = {
  simulationBase: { stock: Array(29).fill(0), index: Array(29).fill(0) },
  simulationThresholds: { '3': { up: 20 }, '10': { up: 100 }, '30': { up: 200 } }
}
const ordinaryOnlyRows = createInputRows(1)
ordinaryOnlyRows[0].stock = '25'
const ordinaryOnlyResult = calculateSimulationRows(ordinaryOnlyRows, ordinaryOnlyDetail)
assert.strictEqual(ordinaryOnlyResult[0].deviation10, '+25.00%')
assert.strictEqual(ordinaryOnlyResult[0].space, '75.00%')
