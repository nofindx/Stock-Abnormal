# Stock-Abnormal

异动查查是由微信小程序前端和 Python 行情服务组成的股票异动计算工具。

## 当前文档

- [产品需求 PRD](docs/异动查查小程序PRD.md)：产品范围、页面需求、数据源、规则和验收基线。
- [设计与工程说明](docs/异动查查-最新设计与工程说明.md)：数据库、任务、接口参数、状态机、失败处理和部署约束。
- [测试规范与用例](docs/测试规范与用例.md)：需求映射、测试案例、API 脚本和微信开发者工具验收。
- [待办清单](docs/待办.md)：部署迁移和最终验收事项。
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

使用微信开发者工具导入 `miniprogram/`。生产环境通过 `wx.cloud.callContainer` 调用云托管服务；本地联调时可在 `app.js` 配置开发环境 API 地址。真实页面编译、刷新和截图验收按测试文档执行。

## 测试

```bash
python3 -m unittest discover -s server -p 'test_*.py'
python3 server/tools/api_contract_check.py
python3 server/tools/api_contract_check.py --require-data --require-shared-redis  # 生产验收
python3 server/tools/prediction_migration_check.py --require-zero-legacy-reads --require-complete-stock-basic  # 迁移验收
find miniprogram -name '*.js' -print0 | xargs -0 -n1 node --check
python3 -m json.tool miniprogram/app.json >/dev/null
git diff --check
```

## 接口速查

```text
GET  /health
GET  /api/stocks/search?q=...
GET  /api/stocks/detail?ts_code=...
GET  /api/monitor?status=current&type=all|risk|severe
GET  /api/predictions?scope=today|next_day
POST /api/predictions/refresh
```

接口完整字段和阶段规则见工程说明；不要依据旧方案文档或旧字段实现新功能。

## 微信云托管部署

服务：`flask-9a5y`。代码源选择 GitHub，仓库 `nofindx/Stock-Abnormal`，分支
`Stock-Abnormal`。控制台构建配置固定为：

- 目标目录：留空（仓库根目录）
- Dockerfile：有
- Dockerfile 名称：`Dockerfile`
- 端口：`80`

根目录 `Dockerfile` 负责安装 `server/requirements.txt` 并启动
`python -m server.app`；`server/Dockerfile` 可在把 `server` 设为独立构建上下文时使用。
云托管生产环境还必须配置 `REDIS_URL`，服务重启后 `/health.data.intradayState.shared` 必须为 `true`、`backend` 必须为 `redis`。发布后先检查 `/health`，再按测试文档执行 API 和小程序验收。生产发布使用微信云托管控制台，不使用 CloudBase CLI。
