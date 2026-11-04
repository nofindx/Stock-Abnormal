"""Exercise serialized backend simulation inputs through the actual JS consumer."""
import json
from pathlib import Path
import subprocess
import unittest

from server.core.abnormal_rules import BOARD_RULES, Board
from server.services.market_service import MarketService


class SimulationContractTests(unittest.TestCase):
    def test_frontend_rows_match_backend_metrics_across_confirmed_resets(self):
        root = Path(__file__).resolve().parents[2]
        dates = ["20261029", "20261030", "20261102", "20261103", "20261104"]
        for board in (Board.MAIN, Board.CHINEXT):
            for context in ({}, {"ordinaryResetDates": ["20261102"]},
                            {"severeResetDate": "20261102"},
                            {"severeResetDates": ["20261030", "20261103"]}):
                with self.subTest(board=board, context=context):
                    stock = [{"date": dates[0], "return": 40.0}, {"date": dates[1], "return": 8.0}]
                    index = [{"date": dates[0], "return": 1.0}, {"date": dates[1], "return": -0.2}]
                    rule = BOARD_RULES[board]
                    rows = [{"date": day, "stock": value, "index": benchmark}
                            for day, value, benchmark in zip(dates[2:], (5, -3, 2), (0.2, -0.1, 0.3))]
                    payload = {"rows": rows, "detail": {
                        "simulationBase": MarketService._simulation_base(stock, index, context),
                        "simulationThresholds": {
                            "10": {"up": rule.severe_10d_threshold.up},
                            "30": {"up": rule.severe_30d_threshold.up},
                        },
                    }}
                    result = subprocess.run(["node", "-e", "global.Page=()=>{};"
                        "const {calculateSimulationRows}=require('./miniprogram/pages/stock/index');"
                        "const p=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                        "process.stdout.write(JSON.stringify(calculateSimulationRows(p.rows,p.detail)));"],
                        cwd=root, input=json.dumps(payload), text=True, capture_output=True, check=True)
                    calculated = json.loads(result.stdout)
                    for position, row in enumerate(rows):
                        stock.append({"date": row["date"], "return": row["stock"]})
                        index.append({"date": row["date"], "return": row["index"]})
                        metrics = MarketService._vector_metrics(stock, index, rule,
                            ordinary_reset_dates=context.get("ordinaryResetDates"),
                            severe_reset_date=context.get("severeResetDate", ""),
                            severe_reset_dates=context.get("severeResetDates"))
                        distances = []
                        for window, limit in ((10, rule.severe_10d_threshold.up), (30, rule.severe_30d_threshold.up)):
                            deviation = metrics["up_deviations"][window]
                            self.assertAlmostEqual(float(calculated[position][f"deviation{window}"].rstrip("%")), deviation, delta=0.0051)
                            self.assertEqual(calculated[position][f"deviation{window}Window"], metrics["up_window_lengths"][window])
                            distances.append(limit - deviation)
                        distance = min(distances)
                        expected = "已触发" if distance <= 0 else "<0.01" if distance < 0.005 else f"{distance:.2f}"
                        self.assertEqual(calculated[position]["space"], expected)
