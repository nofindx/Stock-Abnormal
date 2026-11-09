# Stock-Abnormal

异动查查是由微信小程序前端和 Python 行情服务组成的股票异动计算工具。

## 当前文档

> `20261110`收尾：1.0.20开发版已重新上传启动修正版，前端源码`96538fa`、包大小192229 bytes；替代11月9日首次上传的192005 bytes包。修复微信运行时不支持require(JSON)导致App未注册的问题，本地三页已实际打开且控制台0错误。后端308运行代码不变，配套发布标识仍为20261109.1。新回执及启动证据见[发布验收补记](docs/发布验收_20261109.md)。

> `20261109` 最新：后端`flask-9a5y-308`已部署，小程序开发版`1.0.20`已上传，配套发布标识`20261109.1`。首开/手动全时段获取最新可用报价，返回及切标签不重复自动刷新；线上单股及预测S2已获取11月9日真实报价。源码`2823d4a`，后续文档留档提交不改变运行代码。详见[本轮发布验收](docs/发布验收_20261109.md)及[正式上线清单](docs/正式上线清单.md)。

已上传开发版1.0.20，1.0.18代码审核已通过且原包保持不动；后端预测v74，监控规则`20261102-product-policy-v34`，产品规则`RULE-2026-10-30.1`，本轮未改规则。开发版上传、真机验收、代码审核、备案、认证和正式发布分别记录。详情见[版本发布记录](docs/版本发布记录.md)。

- [产品需求 PRD](docs/异动查查小程序PRD.md)：产品范围、页面需求、数据源、规则和验收基线。
- [详细设计](docs/异动查查-详细设计.md)：业务流程、统一接口、规则计算、数据模型、前后端分工、测试和部署约束。
- [测试规范与用例](docs/测试规范与用例.md)：需求映射、测试案例、API 脚本和微信开发者工具验收。
- [历史待办清单](docs/archive/TODO-临时工作任务.md)：历史部署迁移记录，不作为当前实现依据。
- [生产操作手册](docs/生产操作手册.md)：Redis、基础资料、旧表迁移和健康检查的生产步骤。
- [正式上线清单](docs/正式上线清单.md)：微信提审、隐私合规、真机验收、审核文案和首个交易日检查。
- [专项验收历史方案](docs/vNext专项门禁验收方式_20261107.md)：仅归档原T1–T8方案；暂缓及不必要专项已按用户决定移出当前待办和发布门禁，实际安排见[正式上线清单](docs/正式上线清单.md)。
- [下一大版本架构研究](docs/next-major/architecture/)：当前发布范围之外的模块化架构评审和迁移建议，不作为本版本实现依据。
- `docs/archive/`：历史方案和评审记录，不作为当前实现依据。
- `docs/UI/`：历史 UI 设计稿，不参与运行时构建。

## 目录结构

```text
.
├── docs/                 # PRD、工程说明、测试规范和历史设计
├── miniprogram/          # 微信原生小程序，开发者工具打开此目录
├── server/               # Python HTTP 服务、规则和测试
├── container.config.json # 微信云托管配置
└── README.md             # 项目统一入口
```

## 启动后端

```bash
TUSHARE_TOKEN=你的token \\
MYSQL_HOST=127.0.0.1 \\
MYSQL_PORT=3306 \\
MYSQL_USER=... \\
MYSQL_PASSWORD=... \\
MYSQL_DATABASE=stock_abnormal \\
python3 -m server.app
```

默认监听 `http://127.0.0.1:8787`。Token 和数据库凭证只能通过环境变量注入，不进入小程序包、接口响应或日志。

## 微信开发者工具

使用微信开发者工具导入 `miniprogram/`。生产环境通过 `wx.cloud.callContainer` 调用云托管服务；开发者工具 `develop` 环境默认请求云托管地址，上传的开发版本可直接访问真实数据。需要本地联调时临时把 `app.js` 的 `devApiBaseUrl` 改为 `http://127.0.0.1:8787`，云端验收和上传前恢复云托管地址。真实页面编译、下拉刷新和截图验收按测试文档执行。

## 测试

```bash
python3 -m unittest discover -s server -p 'test_*.py'
node miniprogram/app.test.js
node miniprogram/utils/api.test.js
node miniprogram/pages/monitor/index.test.js
node miniprogram/pages/prediction/prediction.test.js
node miniprogram/pages/prediction/index.test.js
node miniprogram/pages/refresh-lifecycle.test.js
node miniprogram/pages/stock/simulation.test.js
node miniprogram/pages/business-boundary.test.js
node miniprogram/pages/temporal-policy.check.js
node docs/verification/20261108/time-boundary-audit.js
python3 server/tools/api_contract_check.py
python3 server/tools/api_contract_check.py --require-data  # 生产验收
python3 server/tools/prediction_migration_check.py --require-zero-legacy-reads --require-complete-stock-basic  # 迁移验收
python3 server/tools/health_alert_check.py --base-url http://127.0.0.1:8787  # 健康告警检查
find miniprogram -name '*.js' -print0 | xargs -0 -n1 node --check
python3 -m json.tool miniprogram/app.json >/dev/null
git diff --check
```

## 接口速查

```text
GET  /health
GET  /health/ui
GET  /api/stocks/search?q=...
GET  /api/stocks/detail?ts_code=...
GET  /api/market/history-coverage?offset=0&limit=100
GET  /api/market/calculation-bases?ts_codes=...
GET  /api/market/dataset-status
GET  /api/market/jobs
GET  /api/monitor?status=current&type=all|risk|severe
GET  /api/predictions?scope=today|next_day
POST /api/predictions/refresh
POST /api/quotes/realtime
```

接口完整字段和阶段规则见工程说明；不要依据旧方案文档或旧字段实现新功能。

浏览器直接打开 `/health/ui` 可查看人类可读的发布状态摘要；`/health` 默认仍返回机器可读 JSON。浏览器以 `Accept: text/html` 请求 `/health` 时也会显示同一页面，脚本使用 `Accept: application/json` 时不受影响。

## 微信云托管部署

服务：`flask-9a5y`。代码源选择 GitHub，仓库 `nofindx/Stock-Abnormal`，分支
`Stock-Abnormal`。控制台构建配置固定为：

- 目标目录：留空（仓库根目录）
- Dockerfile：有
- Dockerfile 名称：`Dockerfile`
- 端口：`80`

根目录 `Dockerfile` 负责安装 `server/requirements.txt` 并启动
`python -m server.app`；`server/Dockerfile` 可在把 `server` 设为独立构建上下文时使用。
当前云托管沿用腾讯云 Redis 进行短时报价缓存与跨实例请求协调；已发布数据集、公告事实与授权视图由共享 MySQL 管理，盘中结果不覆盖正式收盘数据集。发布后先检查 `/health`，再按测试文档执行 API 和小程序验收。生产发布使用微信云托管控制台，不使用 CloudBase CLI。
