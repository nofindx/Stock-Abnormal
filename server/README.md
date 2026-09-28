# 后端接口

  后端只负责读取 Tushare、缓存行情并执行异动计算。Tushare Token 优先通过 `TUSHARE_TOKEN` 环境变量读取；本机开发时可从 `key/tushareMcp.txt` 的 MCP URL 或 `key/key.txt` 读取。Token 不进入小程序包、不写入响应、不写入日志。

## 启动

```bash
TUSHARE_TOKEN=你的token MYSQL_HOST=... MYSQL_USER=... MYSQL_PASSWORD=... python3 -m server.app
```

默认监听 `http://127.0.0.1:8787`。

## 微信云托管部署

项目根目录已经包含可直接构建的 `Dockerfile`。在云托管服务 `flask-9a5y` 中部署本目录时：

1. 构建上下文选择项目根目录，容器端口填写 `80`，启动命令使用 Dockerfile 默认命令。
2. 在服务环境变量中配置 `TUSHARE_TOKEN`，值只从 Tushare 控制台复制到云托管，不要写入小程序代码或镜像。
3. 配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。单股计算只保存股票/指数收益率向量和任务状态，不建立原始日线表。
4. 配置 `MONITOR_DB_PATH` 到持久化云数据库/云硬盘挂载路径；不要在生产使用 `/tmp`，否则多实例或重启会丢失监控快照。
5. 部署后先访问 `/health`，确认 `tushareConfigured` 和 `marketCalc.available` 为 `true`，再测试 `/api/stocks/search?q=宁德`、`/api/stocks/detail?ts_code=300750.SZ` 和 `/api/monitor?status=current&type=all`。
6. 小程序通过 `wx.cloud.callContainer` 调用服务，服务名必须与小程序 `app.js` 中的 `cloudService` 一致。

监控池已经独立为公告快照链路：后台采集东方财富证券 18.cn 主源和巨潮资讯备源，按四态策略原子发布唯一当前快照：两源成功时 18.cn 为主、巨潮辅助验证；18.cn 失败时由巨潮接替；两源都失败时保留旧快照，无旧快照返回错误空态。用户请求只读取当前快照，不会触发公告采集或全市场行情扫描。系统不接入交易所官方公告接口，不保存公告 HTML 或正文文件；严重异动期内的 PDF 仅按记录缓存到 `MONITOR_PDF_DIR`，结束后清理。

## 接口

- `GET /health`：服务和 Tushare 配置状态。
- `GET /api/stocks/search?q=宁`：股票搜索，最多返回 5 条。
- `GET /api/stocks/detail?ts_code=300750.SZ`：单股状态、偏离和预警。
- `GET /api/stocks/announcements?ts_code=300750.SZ`：查询巨潮资讯中的交易所异动及风险提示公告。
- `GET /api/stocks/300750.SZ/abnormal`：单股接口的 PRD 兼容路径。
- `GET /api/monitor?status=current&type=all|risk|severe`：读取后端上海时间每日 00:00 后生成的当前公告监控快照；失败时仅在次日 01:00-09:00 每小时重试。请求只做快照筛选，不触发公告采集、行情请求或全市场扫描。更新时间为 `YYYY-MM-DD HH:mm`，不提供历史监控接口。
- `GET /api/monitor/announcement?key=...`：读取当前严重异动期内已缓存的 PDF；记录结束后返回 404。
- `GET /api/predictions?scope=today|next_day`：当日或次日预测。首次访问或快照过期时立即返回最近快照，并通过 `refreshing=true` 表示后台正在更新。
- `POST /api/predictions/refresh`：触发指定预测范围的后台刷新，立即返回当前快照；JSON body 为 `{ "scope": "today" }`。客户端应在 `refreshing=true` 时短轮询 GET，避免阻塞等待全市场扫描。
- `GET /api/market/status`：最近交易日和数据源状态。

单股计算基础数据由服务启动的后台线程在上海时间每日 00:00 后更新；前端详情请求不会触发更新。首次空库会初始化最近 30 个交易日，后续只拉新增交易日和新上市股票的必要价格。实时股票与所属指数行情同时成功时才修正偏离值，失败时返回 MySQL 最近结果并带 `dataQuality.message`。监控池中的 30 日严重异动是 30 个交易日回看判定，公告触发后的实际监管期仍为 10 个交易日。

公告快照的 `dataQuality` 会返回 18.cn 主源、巨潮资讯备源和覆盖边界；UI 不得把备源或主源包装成交易所官方接口。
