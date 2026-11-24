# Stock-Abnormal

异动查查是由微信小程序前端和 Python 行情服务组成的股票异动计算工具。

## 当前版本

微信公众正式版仍为 **2.0.3 / 20261115.2 / v75**。云托管已运行 **2.0.4 / 20261124.1 / v76**，派生预测及云端S1/S2核心路径已通过验收；11月25日00:36公告刷新完成，巨潮分页覆盖仍有partial提示，00:54复核尚未消除。2.0.4小程序已上传，待用户真机验收、提审及公开发布，详情见[候选记录](releases/2.0.4/README.md)。备案、认证及隐私指引已完成。广告关闭，10日模拟和次日预测免费。

日常从[文档目录](docs/README.md)进入。正式版源码和验收见[2.0.3 发布归档](releases/2.0.3/README.md)。线上状态和待办见[正式上线清单](docs/05-正式上线清单.md)。

## 目录结构

```text
.
├── docs/                 # 现行产品、开发、测试和运维文档
├── releases/             # 按版本保存发布记录、证据和快照
├── archive/              # Git 历史索引和本机冻结包
├── .local/               # 本机凭据，Git 与容器构建均忽略
├── miniprogram/          # 微信原生小程序，开发者工具打开此目录
├── server/               # Python HTTP 服务、规则和测试
├── container.config.json # 微信云托管配置
└── README.md             # 项目统一入口
```

## 启动后端

```bash
TUSHARE_TOKEN=你的token \
MYSQL_HOST=127.0.0.1 \
MYSQL_PORT=3306 \
MYSQL_USER=... \
MYSQL_PASSWORD=... \
MYSQL_DATABASE=stock_abnormal \
python3 -m server.app
```

默认监听 `http://127.0.0.1:8787`。此示例仅启动服务；S1/S2 还需要签名密钥、已初始化的共享发布表、Redis 及匹配的来源配置，按[生产操作手册](docs/04-生产操作手册.md#11-本地联调与来源配置)准备。环境字段见 `server/.env.example`；凭据通过环境注入。

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
`python -m server.release`，幂等应用选定的共享发布迁移后启动 `server.app`。旧的 `server/Dockerfile` 已移除；根镜像同时安装公告 OCR 依赖。
当前云托管沿用腾讯云 Redis 进行短时报价缓存与跨实例请求协调；已发布数据集、公告事实与授权视图由共享 MySQL 管理，盘中结果不覆盖正式收盘数据集。发布后先检查 `/health`，再按测试文档执行 API 和小程序验收。生产发布使用微信云托管控制台，不使用 CloudBase CLI。
