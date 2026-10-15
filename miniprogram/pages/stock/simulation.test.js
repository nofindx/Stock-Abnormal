const assert = require('assert')

global.Page = () => {}
const { calculateSimulationRows, createInputRows } = require('./index')

const detail = {
  simulationBase: {
    stock: [-1.595428027146073, -11.288566243194207, -6.205673758865249, 5.40933546604625, 0.3172851427783119, -12.307480748074806, -2.540379488787836, -0.933226065969428, 15.332142277082994, 0.9012815096465232, -2.763433356594558, -8.138366585330846, 19.999999999999996, 0.390625, 8.949416342412464, 18.75, -0.7218045112781901, 20.00403918004645, 4.76270615954224, -1.3333333333333308, 1.9619016606968296, 2.259481037924171, 10.360712054965626, 13.47010965688007, -6.47795997256686, 7.453333333333334, -8.81623030152625, -6.674831598285369, 4.403616214639827, 5.572625698324041],
    index: [3.7658607124912535, -1.8500547629862083, 1.3379062879610215, -2.1878816113135025, -1.8171511875105129, -0.3975960138890122, -2.098146111894672, 2.419858845212697, -1.5187023705216407, -0.6874061358065564, -0.6860883911408822, -1.0084129791510188, -1.6176127885134115, 1.5502535847651044, 4.138481573697117, -0.6124729734010681, 2.8851695173508496, 0.29368927485928786, 0.4559439043958502, -0.2515011935436129, -2.347117525427578, -4.062688054297759, 0.8584941044244676, -2.5063236839167446, -4.815785980405163, 0.06472461242996719, -2.096040772226626, 0.03195437708378712, -3.287593869718375, -1.5026154492631782]
  },
  simulationThresholds: { '10': { up: 100 }, '30': { up: 200 } }
}

const rows = createInputRows(2, ['2026-10-16', '2026-10-19'])
rows[0].stock = '16'
rows[0].index = '0'
const result = calculateSimulationRows(rows, detail)
assert.strictEqual(result[0].deviation3, '+32.60%')
assert.strictEqual(result[0].deviation10, '+49.55%')
assert.strictEqual(result[0].deviation30, '+200.68%')
assert.strictEqual(result[0].space, '已触发')
assert.strictEqual(result[0].deviation10Window, 9)
assert.strictEqual(result[0].deviation30Window, 23)
assert.notStrictEqual(result[1].deviation10, '--')
assert.strictEqual(result[1].stock, '0')
assert.strictEqual(result[1].index, '0')

console.log('stock simulation tests passed')

const ordinaryOnlyDetail = {
  simulationBase: { stock: Array(29).fill(0), index: Array(29).fill(0) },
  simulationThresholds: { '3': { up: 20 }, '10': { up: 100 }, '30': { up: 200 } }
}
const ordinaryOnlyRows = createInputRows(1)
ordinaryOnlyRows[0].stock = '25'
const ordinaryOnlyResult = calculateSimulationRows(ordinaryOnlyRows, ordinaryOnlyDetail)
assert.strictEqual(ordinaryOnlyResult[0].deviation3, '+25.00%')
assert.strictEqual(ordinaryOnlyResult[0].deviation10, '+25.00%')
assert.strictEqual(ordinaryOnlyResult[0].space, '已触发')
