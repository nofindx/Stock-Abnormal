# 后端接口

  后端只负责读取 Tushare、缓存行情并执行异动计算。Tushare Token 优先通过 `TUSHARE_TOKEN` 环境变量读取；本机开发时可从 `key/tushareMcp.txt` 的 MCP URL 或 `key/key.txt` 读取。Token 不进入小程序包、不写入响应、不写入日志。

## 启动

```bash
TUSHARE_TOKEN=你的token python3 -m server.app
```

默认监听 `http://127.0.0.1:8787`。

## 微信云托管部署

项目根目录已经包含可直接构建的 `Dockerfile`。在云托管服务 `flask-9a5y` 中部署本目录时：

1. 构建上下文选择项目根目录，容器端口填写 `80`，启动命令使用 Dockerfile 默认命令。
2. 在服务环境变量中配置 `TUSHARE_TOKEN`，值只从 Tushare 控制台复制到云托管，不要写入小程序代码或镜像。
3. 配置 `MONITOR_DB_PATH` 到持久化云数据库/云硬盘挂载路径；不要在生产使用 `/tmp`，否则多实例或重启会丢失快照。
4. 部署后先访问 `/health`，确认 `tushareConfigured` 为 `true`，再测试 `/api/stocks/search?q=宁德` 和 `/api/monitor?status=current&type=all`。
5. 小程序通过 `wx.cloud.callContainer` 调用服务，服务名必须与小程序 `app.js` 中的 `cloudService` 一致。

监控池已经独立为公告快照链路：后台定时采集巨潮资讯公开披露的交易异动公告，解析成功后原子发布 SQLite 快照；用户请求只读取最近一次成功快照，不会触发全市场行情扫描。当前 `designated-disclosure-confirmed` 只表示指定披露平台已确认，不等于交易所非公开重点监控名单；没有交易所官方核验时不得对外宣称完整官方监控结果。生产环境请把 `MONITOR_DB_PATH` 指向持久化云数据库或持久化卷，`/tmp` 仅适用于本地或单实例开发。

## 接口

- `GET /health`：服务和 Tushare 配置状态。
- `GET /api/stocks/search?q=宁`：股票搜索，最多返回 5 条。
- `GET /api/stocks/detail?ts_code=300750.SZ`：单股状态、偏离和预警。
- `GET /api/stocks/announcements?ts_code=300750.SZ`：查询巨潮资讯中的交易所异动及风险提示公告。
- `GET /api/stocks/300750.SZ/abnormal`：单股接口的 PRD 兼容路径。
- `GET /api/monitor?status=current|history&type=all|risk|severe`：读取后端上海时间每日 00:05 定点生成的最近成功公告监控快照；请求只做快照筛选，不触发公告采集、行情请求或全市场扫描。更新时间为 `YYYY-MM-DD HH:mm`。
- `GET /api/monitor-pool?status=current|history&type=all|risk|severe`：监控池的 PRD 兼容路径。
- `GET /api/predictions?scope=today|next_day`：当日或次日预测。首次访问或快照过期时立即返回最近快照，并通过 `refreshing=true` 表示后台正在更新。
- `POST /api/predictions/refresh`：触发指定预测范围的后台刷新，立即返回当前快照；JSON body 为 `{ "scope": "today" }`。客户端应在 `refreshing=true` 时短轮询 GET，避免阻塞等待全市场扫描。
- `GET /api/market/status`：最近交易日和数据源状态。

公告快照的 `dataQuality` 会返回来源、确认状态和覆盖边界；公开披露平台无法覆盖交易所未公开重点监控名单，UI 必须保留这一数据质量边界。
