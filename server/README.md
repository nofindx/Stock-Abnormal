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

监控池已经独立为公告快照链路：后台以东方财富证券 18.cn 为主源、巨潮资讯为备源，按正文语义分类后原子发布唯一当前快照；用户请求只读取当前快照，不会触发公告采集或全市场行情扫描。系统不接入交易所官方公告接口，也不保存公告 PDF、HTML 或正文文件，只保存必要元数据和 URL。生产环境可使用 `/tmp` 作为低成本临时快照目录；容器重启后无当前快照时会立即补采，已有当前快照则按定时策略运行。

## 接口

- `GET /health`：服务和 Tushare 配置状态。
- `GET /api/stocks/search?q=宁`：股票搜索，最多返回 5 条。
- `GET /api/stocks/detail?ts_code=300750.SZ`：单股状态、偏离和预警。
- `GET /api/stocks/announcements?ts_code=300750.SZ`：查询巨潮资讯中的交易所异动及风险提示公告。
- `GET /api/stocks/300750.SZ/abnormal`：单股接口的 PRD 兼容路径。
- `GET /api/monitor?status=current&type=all|risk|severe`：读取后端上海时间每日 00:00 后生成的当前公告监控快照；失败时仅在次日 01:00-09:00 每小时重试。请求只做快照筛选，不触发公告采集、行情请求或全市场扫描。更新时间为 `YYYY-MM-DD HH:mm`。仅支持 `status=current`，不提供历史监控接口。
- `GET /api/monitor-pool?status=current&type=all|risk|severe`：监控池的 PRD 兼容路径，仅支持当前监控。
- `GET /api/predictions?scope=today|next_day`：当日或次日预测。首次访问或快照过期时立即返回最近快照，并通过 `refreshing=true` 表示后台正在更新。
- `POST /api/predictions/refresh`：触发指定预测范围的后台刷新，立即返回当前快照；JSON body 为 `{ "scope": "today" }`。客户端应在 `refreshing=true` 时短轮询 GET，避免阻塞等待全市场扫描。
- `GET /api/market/status`：最近交易日和数据源状态。

公告快照的 `dataQuality` 会返回 18.cn 主源、巨潮资讯备源和覆盖边界；UI 不得把备源或主源包装成交易所官方接口。
