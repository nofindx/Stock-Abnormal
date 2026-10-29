# 异动查查 vNext 详细开发设计

> 版本：DEV-NEXT-20261029.27
> 编制日期：20261028；统一时区：Asia/Shanghai  
> 当前上游：PRD-NEXT-20261029.21、ARCH-NEXT-20261029.20
> 专家评审输入：PRD-NEXT-20261027.6、ARCH-NEXT-20261027.5；原始报告不追认本轮之后的修订。  
> 评审收敛：[综合评审结论与实施计划](./综合评审结论与实施计划_20261028.md)  
> 评审状态：本轮联合复核后条件通过，待用户最终评审；不是用户已批准的最终版。  
> 状态：评审后开发设计建议稿。本文定义目标实现，不声称代码已实现、云端已验收或用户已批准待定产品口径。

## 1. 范围、优先级与决策

### 1.1 设计目标

让任意一项页面结果都能回答：哪只证券、哪个目标交易日、使用哪版规则、引用哪些行情和公告、经过什么重置、为什么呈现这个状态。单股、监控、预测是同一答案的不同查询视图。

覆盖前后端、数据与公告、规则计算、发布迁移、测试运维。保持无登录、无交易、无支付、搜索历史仅本地和模拟结果不上传。保持现有页面功能与已确认视觉，不另做界面改版。

### 1.2 约束和冲突处理

优先级：用户明确需求 → 已确认 PRD/架构 → 经确认的决策记录 → 本开发设计 → 专家建议。专家原文中的“已拍板”不自动构成用户授权。评审报告中的代码片段是设计输入，不直接复制为生产代码。

下列项目需要在 P0 冻结，不能由程序员隐式决定：

| 决策 | 当前依据 | 本文处理 |
| --- | --- | --- |
| Q1 次日可达性 | 用户已确认按建议，且认为现有预测计算无误 | 保持现有次日预测反解计算，不重写算法；现有目标日指数 0% 情景作为预测假设，不是概率或正式事实 |
| Q2 重置边界 | 产品口径已确认；文档待最终评审 | 阈值触及只形成数据预警；已确认普通公告事实直接产生 10 日同向 +1 和 3 日重置，严重公告事实按正式生效日重置四项；行情计算用于补方向、日期审定和核验，不匹配时修正计算/数据，不得否认已确认公告事实 |
| Q3 监管收益构造 | 只核对四板块核心参数、基准映射、公告重置、普通/严重停复牌复牌和除权/复牌首个有效收益日等会改变结果的边界 | 建立精简规则证据表和少量可重算夹具；官方未明确内容标记为产品口径；未核实情形只使受影响结果不可用，不扩建逐证券逐交易日法规数据库 |

以上不阻止公共类型、持久化、任务、公告采集、发布和前端 mock 开发，但阻止未经证据表和夹具覆盖的计算边界进入生产。本文工程选择采用 MySQL 持久化、实例临时 PDF 缓存、同仓库多角色；新版本盘中实时共享启用 Redis，但 Redis 不作为业务正确性或正式数据可用性的前提。

### 1.3 必须保持的不变量

1. 四项指标独立，展示优先级不能删除或抑制其他指标。
2. 已确认的普通异动公告事实直接产生一次正式计次；收盘计算只负责核验，差异必须进入计算/数据异常并修正，不能少计。
3. 一个独立事实一个事件，多来源不重复，日期重叠不自动归并。
4. 当前计算触发、公告监管事实和次日条件预测分别保存。
5. 不可用不能变成安全、零值或成功空列表；有依据的已知严重风险也不能被无关缺数隐藏。
6. 同一读取上下文内，预测每个结果都能在单股中找到同值结果。
7. 已发布输入引用和结果不可修改；失败任务不改变对外发布指针。
8. 未知原因停牌不能猜测清零；未知数据不能伪造交易日或行情时间。

## 2. 模块和依赖

### 2.1 五个后端模块与前端

| 模块 | 输入 | 输出 | 唯一责任与禁止事项 |
| --- | --- | --- | --- |
| 数据 `data` | 行情、证券、指数、日历；公告模块提交的证据和事实 | 版本化输入、仓储和事务接口 | 接入基础数据、持久化和完整性；不决定监管标签、不解析公告 |
| 规则 `rules` | 规则包、证券属性、适用日期 | 不可变 `ResolvedRuleSet` | 唯一口径源；不联网、不访问业务数据库、不读取页面 |
| 计算 `calculations` | 固定输入、已解析事实、规则、目标日期 | 候选、匹配、事件、重置、四项、条件预测与状态语义 | 纯函数；不抓公告、不写数据库、不读当前时钟 |
| 公告 `announcements` | 来源配置、采集范围、正文/PDF | 覆盖批次、证据、事实及修订 | 采集、正文识别、归一与事实身份；不加次数、不直接重置和改页面状态 |
| 业务服务 `services` | 作业命令、查询参数、仓储和模块接口 | 已发布数据集、读取上下文、展示 DTO | 编排、门禁、发布、查询、实时视图；不复制规则算法 |
| 小程序 | 同版 DTO、模拟基础 | 展示、查询、刷新、本地情景模拟 | 不生成正式监管结论，不上传模拟输入或结果 |

`domain` 是共享类型包；`api` 是传输适配层，均不是新增业务模块。发布和查询内聚在业务服务的内部包，不建设独立“状态投影服务”。为减少查询成本可以保存只读展示字段，但它们由统一结果生成，不拥有第二套业务状态。

### 2.2 建议目录与导入方向

```text
server/
  domain/               # IDs、Decimal 单位、状态、不可变 DTO、缺因定义
  rules/                # schema、sets、registry、resolve、规则说明素材
  calculations/         # returns、windows、match、count、reset、forecast、state
  data/                 # adapters、repos、mysql、input_manifests
  announcements/        # collect、parse、normalize、identity、correction、retention
  services/
    jobs/               # 调度、任务、租约、重试与补偿
    build/              # 固定输入、时间顺序重放、生成完整结果
    publication/        # 门禁、封存、CAS、回滚、outbox
    queries/            # 单股/监控/预测/规则/PDF 查询
    realtime/           # 实时采集协调、短期共享视图、过期处理
    presentation/       # 文案、环进度、标签；输入只能是计算结果和事实
  api/                  # 路由、参数和权限校验、统一响应
  migrations/           # 编号 SQL、校验和、运行记录；应用启动不执行 DDL
  fixtures/vnext/       # 版本隔离的真实证据与合成边界
miniprogram/
  store/                # datasetStore、refreshCoordinator、请求代次
  components/           # 状态卡、指标卡、日期栏、PDF 入口、模拟卡
  utils/                # 协议校验、展示 token、纯本地模拟与输入校验
```

依赖：API → 业务服务 → 数据/公告/规则/计算；公告 → 数据仓储接口和 domain；计算 → domain 与规则类型；规则 → domain、校验库。模块装配仅在入口，禁止业务服务互相注入回调以自动重建。采集完成通过持久化任务/outbox 请求重建。

CI 使用 AST/import 检查依赖，不以搜索 `100`、`20` 或 `new Date()` 等普通数字/语法作为违规证据。CSS 尺寸、日期格式化和模拟运算允许；正式规则硬编码、业务时钟推导才禁止。

## 3. 统一类型、精度、日期和版本

### 3.1 基本类型

| 类型 | 约定 |
| --- | --- |
| `SecurityId` | 内部不可变 ID；名称、简称、代码沿革单独有效期记录；禁止用名字含“退”推断交易状态 |
| `TradeDate` | API `YYYYMMDD`，数据库 `CHAR(8)` ASCII；有效日期校验加交易所日历验证 |
| `Instant` | 数据库存 UTC `DATETIME(3)`；API RFC3339 带时区，例如 `2026-10-28T09:00:00.000+08:00`；这是时间戳，不与八位日期混用 |
| `Ratio` | 十进制比例，20% 为 `"0.2"`；正式边界计算使用 Decimal；不得把百分数 20 与比例 0.2 混算 |
| `Price` | Decimal，按证券报价单位量化；金额与阈值不使用二进制浮点判定 |
| API 数值 | 金额、收益、阈值优先十进制字符串；次数整数；进度可用展示 number；缺失为 `null`，禁止空字符串/零冒充缺数 |
| 显示格式 | 日期和时间使用服务端文案；数值最后一步格式化；判定永远使用未显示舍入的值 |

30 个有效日收益点足以构成 30 日复合收益；若从连续收盘点计算，通常需要基准点和 30 个期末点。设计必须明确输入类型，不能用“30 点不足”混淆价格点和收益点。

### 3.2 版本分离

| 标识 | 用途 |
| --- | --- |
| `rule_version` + `rule_hash` | 完整产品规则包与不可变规范内容哈希 |
| `generator_version` | 计算/展示生成代码的提交及构建标识；语义改变即使未改阈值也要评估规则换版 |
| `announcement_parse_version` | 事实抽取规则和 OCR/解析器版本；一个数据集可引用不同历史解析修订，清单逐项记录 |
| `input_manifest_id` + `inputs_digest` | 本数据集使用的全部输入修订和覆盖批次 |
| `dataset_id` | 内部 BIGINT，不变 |
| `dataset_version` | 展示号 `YYYYMMDD-序号`，序号至少两位、可超过 99；不是排序或回滚依据 |
| `pointer_generation` | 每次发布和回滚都递增；前端接受版本次序和 CAS 依据 |
| `api_schema_version` / `presentation_version` | API 和展示契约版本；与业务规则号分开 |
| `realtime_view_id` | 某一发布基线上的不可变临时视图，包含行情和派生结果；不改收盘集 |

规则文件更新不会在生产进程中热改已发布结果。单个数据集只能引用一个完整规则包；该包内可包含按日期适用的历史分段，并不等于混入另一套灰度规则包。

### 3.3 四组状态

```text
trade_state:       normal | suspended | terminated | unknown
security_traits:   ST / *ST / delisting_period 等有效期属性
regulatory.value:  safe | ordinary | severe | null
regulatory.availability: available | unavailable
metric.status:     triggered | not_triggered | may_trigger | unavailable
metric.availability: available | unavailable
```

`ordinary` 表示有效普通异动公告对应的黄色“普通风险（未进监管）”，仅是普通风险事实展示，不代表进入持续严重监管或重点监控；它仍属于监管/风险展示三态中的普通态。监管事实尚未完成确认时保持 `value=null, availability=unavailable`，并在 `reasons` 或事实解析状态中记录 `pending_verification`，不新增第三种数据状态。`regulatory.value=null` 不得映射为浅蓝“安全”。`undetermined` 如用于 UI，只是展示代码，不新增监管业务枚举。`unknown` 只属于交易状态。每项指标同时携带 `current` 和 `next`，并分 `up/down`；正式计数和预测计数分字段。3/10/30 日偏离项的 `availability` 只由其行情、指数、日历和已生效重置依赖决定；10 日同向还需正式事实账本覆盖，不能因普通公告批次未完成而把其他三项改为 unavailable。

一个确定的严重监管事实成立时，即使 30 日行情缺数，仍保留 `regulatory.value=severe`；缺数只影响对应项和依赖它的结论。若只是已知普通事实，且严重项不可计算，可以展示该普通事实，但不能宣称“最高风险已确定为普通”。因此 `confirmedFacts` 与 `regulatory` 聚合结果分开。

## 4. 规则包和数据源

### 4.1 规则包组成

`RuleSet` 必须含：适用交易所/板块/证券特征、生效区间、基准指数映射、收益构造方法、四项阈值和比较符、计次和核验政策、重置政策、交易时段和日期角色政策、预测政策、监管期政策、展示级别和解释来源。当前产品基准映射为沪市主板 `000002.SH`、深市主板 `399107.SZ`、创业板 `399102.SZ`、科创板 `000688.SH`、北交所 `899050.BJ`。规则证据表至少记录板块/情形、参数或计算边界、产品规则依据、生效日期、内容哈希、夹具引用及 `product_policy|pending_verification` 标记；产品规则缺失或冲突时阻断受影响规则包发布。

收益构造只对会改变当前产品结果的边界显式记录：原始收盘/涨跌幅字段、股票与基准指数复合收益、除权处理、普通事项停牌复牌、严重异动相关停牌复牌、复牌首个有效收益日和比较基点。业务日期统一为 `YYYYMMDD`。规则未核实或存在冲突时不得由相邻板块/情形默认继承，受影响指标返回 `unavailable` 并带 `RULE_UNRESOLVED` 或 `RESET_BOUNDARY_UNRESOLVED`。

证据覆盖以四个板块和上述关键情形为分母，不要求每个证券×交易日×情形都有独立策略记录，也不建设穷举所有停牌组合的法规数据库。少量固定夹具必须能重放阈值、普通/严重公告重置、两类停牌复牌和除权边界；未覆盖项在正式定版前阻断对应规则包发布，但不改变当前冻结正式版。

阈值基线继承 PRD §4.2 的板块表；ST、退市整理、上市初期、特殊无价格限制日和映射变更单独解析，不能直接使用板块默认。生产冻结前需逐格核对来源和生效日期。此文不把尚未补齐的官方出处伪写成已查证定论。

配置 Schema 强制：四项完整、方向和比较符完整、日期合法且不冲突、重置范围显式、窗口政策无缺省、正式适用项不得为 pending、引用来源和决策可追溯。UI 的黄色/红色是产品语义，不冒称交易所配色规则。

### 4.2 数据源职责和接入契约

| 数据 | 当前接入方向 | 接入必须产出 |
| --- | --- | --- |
| 盘后静态行情 | Tushare 日频接口 | 股票与指数收盘数据、交易日、源批次与哈希；供正式收盘数据集使用 |
| 盘中实时行情 | 每批最多 100 只；腾讯优先，新浪补缺，东方财富再补缺 | 股票与指数报价日期时间、逐标的实际来源、延迟、字段可用性、失败和回退链；Tushare 不承担盘中实时 |
| 公告列表与正文 | 巨潮主采集；交易所/发行人补充核验 | 发布主体与承载渠道分开、源内 ID、发布时刻、URL、正文证据和覆盖范围 |
| 监管处置及期限 | 正式公告明确内容 | 生效起止、期限单位、认定主体、证据段落；期限缺失不得自造十日监管事实 |

Tushare 用于盘后静态收盘行情、指数、证券资料、交易日历与停复牌资料。盘中行情沿用当前仓库实现的适配器及回退顺序；迁移时以实际代码与运行配置为准，逐只证券/指数保留来源、报价时间和回退证据，不将 Tushare 描述为盘中源。

接入层统一输出 `BatchResult(status, records, scope, expected_pages, completed_pages, next_cursor, source_watermark, received_at, error, quota)`。限额返回不能当作全量成功；批次空记录须有已完成范围的证明。多源回退需校验日期和口径，不能把股票 A 源、指数 B 源时间错位后仍标同一时点。

## 5. 公告、事实、计次与修订

### 5.1 公告流水线

列表分页采集 → URL/源 ID 幂等落账 → 正文/证据解析 → 结构化抽取 → 身份归一/更正关系 → 固定证据的事实修订 → 业务服务调计算匹配。对支持阅读的普通异动公告（包括公司发布的普通异动风险提示/股票交易异常波动公告）、严重异动和退市整理公告，另由监控服务按需把可信来源 PDF 下载到实例临时目录，校验 `%PDF` 文件头后通过受控代理提供小程序内阅读；与本产品定义事实无关的一般风险提示公告不开放 PDF 入口。

标题只决定需要解析，不能单独证明普通/严重异动、方向、监管期间或停牌原因。正文无法读取时保留待核验记录。公告既可能有普通又有严重事实，禁止只因标题更严重而丢弃其他正文事实。

### 5.2 三类身份

| 对象 | 键 | 约束 |
| --- | --- | --- |
| 公告证据 | 来源代码 + 源内公告 ID + 内容修订 | 源内幂等；URL 变化不当然生成新事实 |
| 内容对象 | 二进制 SHA256 | 字节完整性及解析缓存；不是跨源事实 ID；正文哈希另外保存 |
| 业务事实 | `fact_id` + `fact_revision_id` | 一个事实多修订，多来源证据关联；不可变修订供数据集引用 |

普通异动事实的稳定身份字段为 `(security_id, fact_type, fact_start_date, fact_end_date, canonical_fact_ref)`；`canonical_fact_ref` 是跨来源归一后的业务事实引用，不是任一来源的公告 ID。各来源公告 ID、URL 和正文证据只挂在 `fact_evidence`，不能直接作为新的事实身份。若公告正文明确区分同区间的多个独立事实，必须纳入正文事实序号或对应证据定位。方向、阈值结果等可派生或可更正字段不进入身份键，保存在不可变事实修订中。规范序列化 SHA256 仅作查找索引，完整字段仍保存校验。无法建立跨源 canonical 身份时，只给待核验行 ID，不把不同来源直接拆成多个正式事实，也不把空值拼成正式唯一键。

同一身份被重复获取只加证据。区间“包含/相交”不能自动合并，因为用户要求独立且重叠的事实可分别计数。同日不同处置或延期也不能仅按五字段哈希合并：严重处置、停复牌等使用各自身份策略、处置文书号/有效期和修订关系。冲突不猜测归并，进入核验队列。

更正/撤回：保留原证据与原事实修订，追加新修订和 `supersedes/retracts` 链。新数据集选择新的事实视图，并重算受影响日期。历史数据集仍引用原修订，不原地把事件改为无效。新版本查询可附更正说明；旧版重放显示“当时所知”，另可显式发起“按更正事实重述”，二者不能混名。

### 5.3 正文与行情的匹配

`match_ordinary_fact(fact_revision, fixed_market_inputs, rule_set)` 输出：

```text
MatchResult:
  fact_revision_id, status(matched|pending|conflict|not_applicable)
  security_id, fact_start_date, fact_end_date, direction
  computed_deviation, threshold, comparator, calculation_input_hash
  direction_source(body|market_calculation), evidence_refs[], reasons[]
```

以正文确切区间复算，结束日必须等于 `fact_end_date`，不能挑其他能达标的 3 日窗口替代。当前规则包的普通异动默认要求连续 3 个有效交易日；只有规则包为特定事实类型明确登记了不同区间政策和 `source_ref`，才允许其他区间，不能由匹配器自行放宽。正文有方向时以正文为事实方向；正文未写方向但区间可靠时，由同区间证券行情、规则指定基准和适用规则版本补足方向。该计算既不能更换公告事实区间，也不是公告确认、计次或重置的门槛。依赖齐全仍无法确定方向时才待核验；方向源修订应追加事实修订，不创建第二个事实。正文严重异动事实不要求“今天仍达 100%”才能保存：它与当前计算结果的匹配、监管有效性分别处理。

确认计次必须满足：身份和正文证据完整、适用规则已解析、事实未被撤回或替代；公告正文确认的每个独立普通事实直接产生一个 `SameDirectionEvent`。对应收盘计算达标与否只形成核验结果；不一致或缺失必须记录计算/数据异常并修正，不能取消该事件。一公告可以有多个这样的事实。

正式同向事件只接受 3 日偏离类普通异动事实（`ordinary_deviation_3d`）；换手率、单日偏离和其他事件不计入。公告正文只有明确区分多个独立 3 日区间/事实标识时才可拆成多个事实；只有模糊“多次”描述时保持待核验，不让解析器推断计数。

### 5.4 同向计数

```text
window = 最近 10 个交易所交易日，终点为目标交易日 D
active_events = 数据集选定的正式事件修订
保留 window.start <= fact_end_date <= D 的事件
应用已确认且对 D 生效的严重清零边界，排除边界之前的事件
按 direction 分组，对 fact_id 去重计数
```

仅 `fact_type=ordinary_deviation_3d` 的公告事实可创建正式同向事件；换手率、单日偏离及其他类型不得创建该事件。一篇公告只有明确列出可独立定位的多个 3 日区间/事实标识时，才创建多个事件；模糊“多次”作为待核验事实，不拆分。窗口使用交易所日历，不因个股停牌延长为“该股十个有成交日”。事实区间可以起于窗口外，只要终点入窗即按现 PRD 计入；不再用整个区间必须在窗内或起始日入窗。监管事实有效期只决定当前监管状态和重点监控展示；`SameDirectionEvent` 是否计数只由 `fact_end_date` 是否入窗、严重清零边界以及事实是否被撤回/替代决定，监管期结束不会自动移除仍在 10 日窗口内的事件。

受影响未核验事实/覆盖缺口存在时：可返回 `confirmed_count` 作为有证据的下界，但 `exact_count=null`、`availability=unavailable`、`reasons` 非空，不能显示为精确的 0 次。若已确认下界本身达到严重次数阈值，可保留“已知达到阈值”的证据，精确次数仍标待核验；该推理需专门夹具，不允许变成随意忽略缺因。

### 5.5 覆盖与缺失

覆盖清单按“来源 × 公告类型/栏目 × 交易所/板块 × 查询日期范围”记录，附关键词/过滤条件、页数或总量、游标、水位、请求状态和失败记录。只有分页到末页、预期分页/总量校验通过、查询水位覆盖目标时段、关键来源请求成功且不存在未处理的可定位失败，才将该具体范围标为完整。`not_found` 仅是特定查询范围没有命中；完成且覆盖校验通过后，只能确认该范围没有发现新增事实，不得包装成全市场所有来源、所有类型均无公告。公告覆盖完整性仅约束监管事实与正式计次的“无新增”判断，不阻断行情输入齐全时的当前/次日计算结论。

夜间必须有补采、补正文和补匹配，建议 00:30/03:00/08:30 分轮，具体频率以配额验证冻结；白天与盘后增量继续接入。更正与延迟公告触发新数据集版本，不只更新单页。已知可定位到个股的失败隔离到该股；无法定位且波及全范围的关键采集失败阻断发布，不能把影响偷偷缩小。

## 6. 收益、窗口和重置

### 6.1 收益构造与窗口

`build_returns` 输入原始 `close/pre_close/pct_chg/adj_factor`、指数行、公司行为和收益方法版本，输出可追溯的日收益。禁止在数据缺失时静默切换收益算法。四板块映射、复牌首个有效收益日/比较基点及除权边界按 §4.1 的精简证据表和固定夹具核验；不要求逐证券逐交易日建立策略记录。

给定已验证的股票日收益 `r_s,t` 与对应指数收益 `r_i,t`：

```text
G_s = ∏(1 + r_s,t)
G_i = ∏(1 + r_i,t)
deviation = (G_s - 1) - (G_i - 1) = G_s - G_i
```

若该适用规则明确要求其他累计方式，必须使用版本化构造策略，不能把本公式无条件宣称为全部市场官方定义。`window_policy` 明确 `last_k` 或 `max_within_k`，禁止现网“一条路径固定窗口、另一条路径最大子窗口”的隐性差异。精确区间事实匹配不使用 `max_within_k` 搜索。

停牌日不填收益零、不按周一至周五猜交易日。交易所日历、证券可交易日、收益有效日分开。指数须对齐股票收益所覆盖的同一期间，不得因为停牌就随意漏掉指数期间变化。

### 6.2 重置输入与输出

```text
derive_resets(fact_view, suspension_view, calendar, rules) -> ResetBoundary[]
ResetBoundary:
  boundary_id, trigger_fact_revision_ids[], source_evidence_ids[], metric_keys[]
  policy_id, policy_version, trigger_trading_date, source_published_at
  effective_date, effective_date_basis, decision_status
  first_return_date, baseline_date, baseline_price_ref, baseline_index_ref
  clear_events_before, market_check_status, market_input_hash
  supersedes_boundary_id?, decision_hash, recorded_at, reasons[]
```

`effective_date_basis` 枚举为 `body_explicit_date|fact_interval_rule|publication_time_next_trade_day|official_resumption_rule`；不得保存无依据的自由文本“人工决定”。人工校正必须用带复核人、理由和证据引用的新决策修订表达。事实及证据均 append-only；公告更正/撤回通过 `supersedes/retracts` 建立新修订，并生成新 dataset 重放受影响的事件、边界和指标。旧 dataset 保留当时事实视图和结果，不原位更新或删除。事实成立状态与重置日期审定状态分开；事实已确认但边界仍待审定时，事实不撤销，边界及依赖该边界的指标为 pending/unavailable。

| 事件 | 生效日 | 偏离基线范围 | 同向事件 |
| --- | --- | --- | --- |
| 行情计算普通达标、无公告事实 | 无正式重置 | 保留计算预警 | 不新增正式计次 |
| 普通异动公告事实确认 | 结合事实和规则审定的生效日 | 仅 3 日 | 公告确认的独立普通异动事实按公告方向 +1；对应 3 日收盘计算仅做核验；已确认的其他事件保留 |
| 严重公告确认 | 结合公告正文、市场触发数据和适用规则审定的生效日 | 3/10/30 全部 | 生效日开始清空此前同向事件；历史账本不删 |
| 其他公告且未确认本产品定义的异动事实 | 不产生异动重置 | 不新增异动计次 | 不变 |
| 普通事项停牌后复牌 | 复牌有效交易日 | 3/10/30 新基线 | 保留事件并按终点日自然滚动 |
| 因严重异动停牌/明确严重相关停牌后复牌 | 复牌有效交易日 | 3/10/30 新基线 | 与严重政策一起清零 |
| 停牌原因无法确认 | 边界未决 | 只让受影响项待核验 | 不删除或猜测旧账本 |

时间相邻不足以确认严重停牌原因。临时停牌不自动等价跨日复牌重置，须由适用政策和正式事件区分。普通/严重同日生效取指标范围并集，保留全部证据，不执行两次累乘或重复清零。

有效日和首个收益日分别保存。公告事实确认后只创建事实和重置边界；到达正式 `effective_date` 时才应用重置。普通公告事实同时直接贡献一次 10 日同向计数。严重公告不得直接取公告页面标注日期作为生效日，须结合公告正文的事实类型、所述区间/日期、公告实际发布时间和适用规则审定，并使用可用的收盘行情核验触发日。实际触发日期 D 已由收盘行情确认，且 D 日收盘后/晚间公告正文确认同一严重异动事实时，重置生效日为 D 后的下一个交易日 D+1。公告正文或适用规则明确给出其他生效日时，保存证据并按其执行；页面日期与正文或市场事实不一致时，保留原始值并记录校正值、依据和审计链。公告事实、事实区间及适用规则足以确定正式生效日时，行情计算只做核验和差异诊断，不得作为重置门槛；行情缺失或计算不匹配不得阻止已确认事实在已确定生效日重置。只有公告事实关联、事实区间或正式生效日无法由公告正文/规则/可靠时序证据审定时，才输出待核验并暂不写入重置边界。严重重置从 `effective_date` 开始应用全部四项：偏离基线开启新周期，10 日同向清除生效日前事件，历史事件账本不删除。计算差异须修正系统计算/数据，不能反向否定公告事实。复牌首个收益的比较基点按适用规则、规则证据表和复牌事实确定；未核实的个案不得套用默认口径，不能把生效日之后第一天数据缺失直接当零收益。

### 6.3 重置后窗口未满

重置边界只有在公告事实确认并到达正式生效日后才成立。成立后以新基线从 0 开始，不沿用旧偏离值；接口持续输出重置基线、重置后累计偏离值、实际窗口 `m/k` 和边界证据。仅有行情触及时，不得写入 `reset_boundary`。预警状态和重置事实是不同输入，不允许用 `triggered=true` 推导公告确认或重置。

| 状态 | 显示数值 | 阈值判定 |
| --- | --- | --- |
| 重置已确认、无重置后收益 | 显示重置基线 0%，标注新基线日期 | 不代表公告以外发生了新的指标触发 |
| 重置已确认、有 `m<k` 个完整有效收益点 | 显示从新基线累计的真实值及 `m/k` 实际窗口 | 不能把部分窗口伪称为完整 `k` 日阈值；行情触及不得反向生成重置 |
| 已满 `k` 个有效收益点 | 按完整名义窗口和规则阈值判定 | 正常计算 |
| 重置边界或重置后必要行情/指数缺失 | 显示对应项不可用及缺因，不造零 | 不判阈值 |

已确认的产品边界是：计算触及只形成预警；公告事实决定监管状态和重置；当前/次日计算安全不等待公告批次完成；普通异动公告直接计入 10 日同向并重置 3 日；严重异动公告按正式生效日重置四项，行情计算用于补方向、日期审定和核验，不匹配即修正系统计算/数据，不能否认已确认公告事实。公告披露日/采集日不自动视为生效日。严重公告日期须按公告正文、收盘触发数据和适用规则审定：D 日收盘触发且当晚公告确认同一事实时，D+1 生效。窗口实际长度按指标规则计算。

### 6.4 时间顺序计算与迟到事实

不能先套“最后一次重置”再反过来核验历史公告，否则会抹掉用于证明重置的区间。回放从足够早的已验证检查点开始：按事实结束日计算候选，按披露/生效顺序匹配和激活边界，再计算后续日期。迟到公告生成新的重述数据集；历史“当时所知”输入保持不动。相同生效日事件按确定排序处理并合并范围，边界当日新事实是否计入按 `clear_events_before` 的严格小于关系处理并测试。

## 7. 当前结果、次日预测与筛选

### 7.1 当前结果与正式事实分离

价格偏离在收盘或带时刻盘中行情上计算达到阈值，可给对应四项 `triggered` 预警；监管状态和进入重点监控必须有公告确认事实。3 日计算触发黄色，计算与公告不一致时保留公告事实并修正计算/数据。10 日同向的 `confirmed_count` 来源于已确认普通公告事实；盘中计算候选不预先创建公告事实或正式计次。

`MetricResult` 至少包含 `rule_key, role, target_date, direction, level, status, availability, value, threshold, comparator, actual_window, assumptions, event_refs, reset_refs, reasons, presentation`。一条指标的计算可用性与其公告确认可用性分别记录，不用一方失败抹掉另一方已知数值。

### 7.2 次日反解建议（Q1）

N 窗口保留部分的股票和指数复合因子为 `A_s,A_i`，N 指数假设收益为 `q`，目标偏离阈值为 `h`：

```text
deviation_N(r) = A_s × (1+r) - A_i × (1+q)
r_star = (h + A_i × (1+q)) / A_s - 1
```

`A_s` 必须大于零。先按 N 的目标窗口滚动、重置和映射政策截取保留部分，再反解，不能直接使用 D 的完整十日因子追加一日而变成十一日。存在多个规则允许窗口时分别求解并保存使条件成立的证据窗口。

上涨条件为 `r >= r_star`，下跌条件为 `r <= r_star`，比较符以规则包为准。与 N 日证券合法价格集合求交；触发价格用正式未复权参考价和报价单位映射，再在相邻价格 tick 上正向验算。`r_star < 0` 不等于 D 已触发，也不能从上涨预测排除：目标窗口滚动后，N 小幅下跌仍可能达上涨阈值。

默认建议 `q=0`，字段明确显示“假设指数不涨不跌”；这是条件可达性，不是发生概率或投资建议。无确定价格限制时，不捏造 10%/20%/30%；依赖价格界限的可达性结果不可用，当前真实偏离仍可计算。

### 7.3 次日同向条件预测

1. 先将正式事件窗口滚动至 N，并应用 N 已知生效的清零边界，计算 `confirmed_count_at_N`。
2. 在 N 的规则允许 3 日区间内，按 §7.2 判断同方向普通阈值在合法价格集合中能否达到。
3. 若有可达窗口，且 `confirmed_count_at_N + 1 >= severe_count_threshold`，返回 `may_trigger`，带候选区间、所需价格和 `requires_announcement_confirmation=true`。
4. 多个假设窗口不因此假设新增多次，最多表示一个新的普通事实候选；未来公告是否描述多个独立事实，不在预测中预造。
5. 不写正式事件、不提前重置、不生成正式监管事实；“当前 3 次、条件下可能达 4 次”不显示为当前已有 4 次。

若滚动至 N 后旧事件出窗，必须先扣除再 +1。若 N 已知严重公告重置生效，同向不能把清零前的次数继续带到预测里。当前 D 的严重触发本身不会自动制造正式公告重置或让其他三项消失。

### 7.4 预测只是统一结果的查询视图

全市场上涨方向筛选：10 日同向、10 日偏离、30 日偏离的当前触发/可达候选；有效严重监管期间，另纳入 3 日普通候选。单股始终保留上下两侧完整结果；预测不改变单股判定。

**已确认的展示规则：** N 预测名单不重复展示 D 当日已触发的同一严重规则；其他独立规则仍单独判定和成卡。例如 D 已触发 10 日偏离，N 不重复出同一 10 日偏离预测；若 30 日偏离在 N 仍可能触发，则保留 30 日预测。预测政策返回 `ALREADY_TRIGGERED_CURRENT_SAME_RULE` 排除码和对应 D 触发引用。该筛选只影响预测列表，不改基础 `MetricResult`、单股四项当前/次日预警、正式事实或重置。若正式重置导致 N 窗口/基线改变，则按 N 的真实新基线计算，不能只拿 D 标签机械抑制 N 的规则结果。

空列表响应必须有 `coverage, exclusions_summary, data_availability, empty_reason`。不允许在范围不完整时只返回“暂无风险”。明细排除按需分页，不把五千只股票的全部排除原因塞进首屏。

## 8. 状态合成与页面契约

### 8.1 风险与事实优先级

监管事实只由当前有效公告事实决定：有效严重事实优先于有效普通事实；没有已确认的有效事实不等于已确认“无事实”，须同时携带公告覆盖状态。四项当前/次日计算安全或风险按各自行情依赖独立得出，不等待公告批次；公告源待核验不得覆盖可确定的计算结论。四项行情计算触及及次日可达性只进入预警/预测结果，不得升级监管事实；正式事实不会因价格回落或单项缺数消失。普通计算候选尚无公告确认时，保留对应黄色计算预警及公告核验信息，但不得伪称已形成普通监管事实。

次日结论：已知任一严重项可能触发 → 严重风险；否则，严重项均明确不触发且已知普通项可能触发 → 普通风险；四项均可用且明确不触发 → 安全；其余 → 暂不可用。已知严重风险而其他项不可用时，保留严重风险并附缺因；安全必须以四项计算依赖完整为前提。当前 D 结论按同一原则，但行情计算不依赖公告批次完成；监管事实有效性和覆盖质量单独返回。

### 8.2 单股页面

股票状态卡 → 四项预警卡 → 模拟计算卡，位置及已确认视觉保持。

| 条件 | 卡片/提示 |
| --- | --- |
| 当前目标日已计算触发严重项 | 红边浅粉；“当日已触发严重异动”；按行情日期、已触发详细规则；尚无正式监管事实时红色加粗“将进入重点监控池，具体以官方公告为准” |
| 有效严重监管事实、当前未新触发 | 红边浅粉；“严重异动监管中”；正式事实及期限，另列次日结论；不能仍叫昨日当日触发 |
| 当前无有效严重监管事实，但有有效普通异动事实 | 黄色标签“普通风险（未进监管）”；显示公告事实区间/有效期；过期后隐藏该提示但保留正式计次、重置和历史证据 |
| 无当前严重，次日存在严重可能 | 严重胶囊；“次日可能触发：[详细严重规则]”；普通项不混入摘要，但四项卡仍显示 |
| 次日只有普通可能且严重项明确不触发 | 黄色胶囊；“次日可能触发：[3 日详细规则]” |
| 四项次日全部明确安全 | “暂无异动风险”；不受仍在监管事实内这一事实强行改写为次日风险 |
| 结论不可用 | 提示原因和“次日预测暂不可用”；不凭此新增交易“未知状态” |

交易标签：正常交易不占标签；停牌、终止上市、退市整理、ST 属性按接口顺序展示。时间上不可能同时成立的当前事实不能并列造矛盾；历史事实可在历史区查看。当前只取最严重事实摘要，保留展开证据历史。

### 8.3 四项、颜色与百分号

固定四项，每项 current/next、上下两方向、阈值、实际区间、当前数值、触发条件、环进度及缺因由服务端给定。当前达到普通阈值黄色，严重阈值红色；可能触发的级别相同，但不将 next 卡整体染粉。不可用颜色中性且有原因，零值必须有计算或重置依据。

环的 `progress_pct` 由后端按政策计算并限幅到 0–100；真实数值不截断。次数用 `confirmed_count/limit`，不冒用百分比单位。不能偷偷保留 66.67% 就变黄的旧规则；若产品希望“接近阈值”另立明确政策。百分号强制可见，紧凑字号保持已确认的 20rpx；长偏离值不得省略。

### 8.4 模拟计算

仅 10/30 日偏离；默认空值计算按 0%，输入原文本保持空。未来日期只显示月日；输入框沿用已调整宽度、居中和字号。后端给已按重置处理的窗口向量、未来交易日、阈值、规则与模拟算法版本。前端不重新实现公告匹配或重置分类。

JS 模拟采用确定精度的十进制工具/整数标度，或采用与后端同一规范的数值算法并通过边界夹具；不以任意 epsilon 改变阈值含等号语义。`rule_version` 和 `simulation_algorithm_version` 必须被当前客户端支持，否则禁用模拟并显示原因。新规则包但算法兼容时可通过能力声明支持，不能要求所有配置版本一变都重新上传客户端。

用户输入和结果仅内存保留，不进入请求、日志、埋点或生产库。模拟卡展开/折叠等统计如实施，仅采用无用户标识汇总，不新增未披露采集。

## 9. 时间上下文与三页同步

### 9.1 必须分开的日期

`close_as_of_date` 是最后完成收盘数据日；`business_date` 是当前展示交易日；`target_date` 是指标所指交易日；`next_business_date` 是下一交易日。公告披露日、事实结束日、重置生效日、监管起止和采集时刻均独立保存。

09:00 切换由 `resolve_time_context(calendar_revision, server_now)` 纯函数决定，不依赖定时任务成功。交易日开盘前 09:00 以前可继续上个交易日展示上下文；09:00 起 business_date 为当日；非交易日使用最近完成交易日和下一交易日。展示政策的准确休市边界随规则包冻结，不能仅按日期大小推导。

| 时段 | 当前结果 | 下一交易日结果 | 实时与标签 |
| --- | --- | --- | --- |
| 交易日前 09:00 | 最近完成日 D 的结果 | N 的条件结果 | 保留 D 的触发标签到 N 09:00 |
| 当日 09:00 至实时有效报价前 | 当前业务日已切换；旧收盘只作最后结果 | 对新 N 的数据未就绪明确不可用 | 不把昨日触发改成今日触发，不伪造报价 |
| 配置允许的竞价/连续交易时段 | 同一盘中视图下当日结果 | 次日列表按产品政策关闭 | 首开/主动按钮可请求 S2，午休不回源 |
| 15:00 后正式收盘集未发布 | 最后盘中数据标临时，或上一收盘标历史 | 等待当日基线，不能显示旧日期的新次日预测 | 不用实时快照伪造正式收盘集 |
| 新收盘集发布后 | 当日正式四项 | 下一交易日条件结果 | 统一指针可用，页面取新上下文 |
| 周末、假日、临时休市 | 最近完成交易日 | 下一交易日 | 无实时刷新；事实自然日期有效性照常判断 |

### 9.2 读取令牌

`ReadContext` 固定 `(channel, pointer_generation, dataset_id, pool_version, pool_hash, rule_hash, calendar_revision, time_context_id, realtime_view_id?, expires_at)`。S1 响应的 `read_token` 是 `{value, expires_at}`；`expires_at` 取令牌自身 TTL、`time_context.next_switch_at`、实时视图可展示截止时间（fresh/stale 合约中的 `stale_until`）、数据集/名单撤销或不兼容切换时间中的最早值。fresh 结束后即使 token 尚有效，stale 也只可展示，不能参与当前结论。API 每个请求只读取一次指针；跨页通过令牌固定同一上下文。游标包含该元组和查询条件哈希。任一绑定内容先过期，统一返回 `409 context_expired`，客户端整体重取 S1，不拼接新旧结果。

令牌可为服务端签名、无用户身份的短期上下文，不建立账户。过期、09:00 角色切换、数据集被紧急撤销或实时层到期，返回 `409 context_expired`；前端重新取 S1，不拿旧结果配新日期。令牌固定版本不保证所有独立请求在同一毫秒到达，但保证同令牌结果完全一致。

监管倒数由服务端按正式事实期限单位计算。自然日显示 inclusive 的 `max(end_date - Shanghai_today + 1, 0)`；如正式处置明确交易日则使用交易所日历，不能把两种期限统一成自然日。官方日期区间不明确则显示待核验，不擅定监管起止。

## 10. S1/S2 与预测名单实时视图

### 10.1 前端编排

预测页每次首次进入会话执行两个逻辑刷新阶段：S1 刷新并读取当前最新已发布当日股票池数据集，固定 `dataset_version + pool_version + pool_hash + pool_count + rule_version + time_context_id`，再用该 S1 `read_token` 读取预测首屏列表；这两个调用合称 S1 阶段，不能分别取不同指针。S1 阶段成功且 `intraday_eligible=true` 后执行一次 S2，取得该固定股票池及其指数的盘中实时覆盖，并用 S2 返回的新 `read_token/realtime_view_id` 整体替换首屏结果，不允许把新行情卡片拼进旧列表上下文。两阶段分别可观察、会话内各执行一次，不限定 HTTP 请求数。S1 任一必要调用失败不得启动 S2；S2 不改池、不增删候选、不生成 next。单股详情也先执行 S1，再在适用交易时段只获取所选证券和指数。`onLoad/onShow` 合并同一次进入，不用设备永久标志。切后台恢复或再进入时，依上下文有效期及节流重新确认，不能永久跳过；同一会话并发请求去重。

主动刷新图标执行 S1 并在允许时 S2；单股首页和详情下拉保持关闭。监控/预测若保留原下拉，建议仅 S1；不在无用户确认下改成新增交互。所有按钮动效结束与请求完成绑定，减少动态效果偏好下禁用持续动画。

S2 失败：保留最近成功结果并标 as_of、来源、过期及失败原因；从未成功则当前盘中结果不可用。不得把最后收盘声明为实时成功，也不得无理由涂安全色。`published_at`、行情 `quote_as_of`、本次请求时间分别存在，未返回具体时间时不拼 15:00。

### 10.2 跨实例共享与预测刷新范围

**产品约束：盘中只刷新已发布当日预测名单内的股票；当日尚未收盘，不考虑次日。不设计盘中全市场扫描，不因“可能新增名单外股票”而扩张刷新范围。**

- 预测刷新：S1 读取已发布股票池而不重建/改写它，并返回固定 `dataset_version,pool_version,pool_hash,pool_count,rule_version,time_context_id`；S2 以该名单为范围刷新其中股票及对应指数。两个逻辑阶段必须分别记录 `stage/status/as_of`；同一会话和相同跨实例幂等键不得重复构建股票池或重复拉取相同行情。
- 名单内分批：首屏可以优先请求，剩余已有预测股票按需或分批刷新；分页只是展示/传输策略。覆盖状态仅声明“当日预测名单”，不声明全市场盘中扫描完成。
- 单股详情：仅刷新用户选中的证券及对应指数；其结果与预测若指向同一证券、相同上下文，应完全一致。单股计算不能反向触发全市场任务或为预测名单自动加股。
- 次日结果：未收盘期间不执行次日预测刷新，也不利用 S2 盘中价重建次日条件；按既定产品时段显示暂不开放。正式收盘数据及必要公告就绪后，由收盘构建流程计算并发布次日名单。

`RealtimeView` 冻结基线数据集、报价修订、每只证券与指数时刻、完整范围、缺失范围、结果及过期时刻。股票/指数在允许最大偏差内配对；`as_of` 不使用一个最大时刻掩盖其他证券过期。源回退只切受影响批次，实际来源逐项可追。

新版本启用腾讯云 Redis，目标是解决多实例间重复行情请求和临时结果不共享，而不是将缓存变成真相源。MySQL 保存收盘数据集、事实、正式结果和持久任务；Redis 仅保存带版本的短时行情、盘中视图及短租约/限流状态。盘中视图只覆盖已发布当日预测名单或单股所选证券及其指数，不扫描全市场，不重算 next，不进入公告账本，不覆盖正式收盘集。

**键空间与数据边界：**所有键均带环境、schema 前缀和版本。Redis 分成三类逻辑键，彼此不得复用语义：①报价缓存 `quote_key=SHA256(instrument_id|source_policy|quote_bucket)`，按证券/指数和报价粒度复用；②上游请求合并/短租约 `singleflight_key=SHA256(normalized_instrument_set|source_policy|market_phase|quote_bucket)`，只用于避免重复回源；③读取视图授权 `view_scope_key=SHA256(dataset_id|pool_version|pool_hash|rule_hash|time_context_id|target_date|role|market_phase|scope_hash|source_policy|quote_bucket)`，完整绑定本次读取上下文。仅视图发布使用单调 fencing；报价缓存和上游请求合并不使用 view fence。不可变视图暂存键为 `sa:{env}:rtv:v1:{view_scope_key}:{generation}`，活动视图引用键为 `sa:{env}:rtvp:v1:{view_scope_key}`；持久 fence 主键与视图键使用同一 `view_scope_key`，并逐字段校验上下文。不得仅以 `scope_hash` 或各自从 1 开始的 generation 隔离视图。不得把 openid、搜索词或公告全文放入 Redis key/value。`RealtimeView` 主键和元数据保存完整视图上下文及 `generation`；名单视图仅允许完整 pool 或该 pool 的有声明分批子集。单股和预测共用合规证券级报价缓存，但请求协调和读取视图各自按其语义隔离。

**初始有效期（配置项，不是监管规则）：**行情缓存 TTL 3 秒；实时视图共享保留 TTL 60 秒，但报价超过 5 秒不再视为“新鲜”；最多可作为带明确时间戳的最后成功结果显示至 30 秒，超过 30 秒必须返回实时不可用/更新中，不得标为实时有效。实际有效性还须检查报价源时间戳、股票/对应指数时间差和交易阶段。首期上线前用模拟源并发压测和云端小流量验收确认这些值；任何调整必须同时更新配置说明和测试阈值。

**租约与等待初始值（工程配置，不是产品/监管规则）：**Redis 请求租约 10 秒，每 3 秒 compare-token 续约；单次盘中上游请求超时 7 秒；前端同步等待预算 1.5 秒，持久排队最长 60 秒或最多 5 次可重试尝试，以先到者为准。MySQL job lease 初始 30 秒、worker 心跳 10 秒；Redis lease 仅覆盖一次短实时操作，MySQL lease 管理持久 job，二者互不替代。以上是实施基线，须在云端并发/超时故障注入后冻结实测配置；门禁未过不得启用 Redis 刷新，不得让各 worker 自行发明不同常量。

`read_token.expires_at` 取令牌 TTL、time context 的 `next_switch_at`、实时视图可用于当前结论的截止时间及数据集/名单撤销边界中的最早值；该时间随响应返回，过期统一 `409 context_expired` 并整体重取 S1。

**跨实例单飞：**同一预测刷新按规范化证券/指数集合、来源策略、市场阶段和报价桶生成 `singleflight_key`；单股刷新集合为所选证券及其映射指数。不同报价集合、来源策略、市场阶段或报价桶不得合并。读取视图仍按完整 `view_scope_key` 隔离；S1/S2 token 和视图不可因报价缓存复用而串上下文。S1 客户端以每次页面进入生成稳定 `entry_id`，网络重试复用该 ID；服务端以 `entry_id` 为幂等记录主键，原子保存首次请求指纹及首次解析出的 context 快照。在幂等记录保留期内，相同 entry_id、相同请求指纹重放首次 context，即使指针已切换；context 已过期时返回 `409 context_expired`，客户端创建新的 entry_id 重取 S1。相同 entry_id 但请求指纹不同返回 `409 idempotency_conflict`。entry_id 不得在记录到期后复用；新页面进入必须生成新的 entry_id 并读取当时最新指针。S2 客户端稳定 `request_id`/HTTP `Idempotency-Key` 必须在超时重试时复用；服务端保存 `request_id + canonical_request_fingerprint`，同 ID 同指纹重放首次 job/结果/token，不同指纹返回 `409 idempotency_conflict`。幂等记录至少保留 24 小时且不得短于最大排队/重试期限。视图执行者通过 `SET NX PX` 获取与 `view_scope_key` 关联的短租约，租约值为随机 owner token；仅视图发布流程维护单调 fence。执行者先把完整不可变视图写入 generation 隔离的 Redis 暂存键并设置 TTL；随后在 MySQL 事务中校验 owner/fence/job/完整上下文并 CAS 授权 `active_realtime_view_id + generation`。Redis 活动指针只作为可丢失的缓存提示，不能单独使视图可读；若活动提示领先、滞后或丢失，读取仍以 MySQL 授权的 view_id/generation 为准，提示不匹配则返回 queued/unavailable/context_expired。部分暂存数据不可见，不能在 Lua 中循环写大型结果。释放租约仅允许 compare-token 后删除。等待者以带抖动退避读取活动视图；等待预算耗尽返回 202/queued 或有明确时戳的有效旧结果。有效租约内同 key、同来源、同批次最多一次上游调用；租约接管时每个 `singleflight_key + source + quote_bucket` 最多允许一次重发（该来源批次最多两次上游调用）。若重发仍未完成或再次失去租约，返回 queued/unavailable，不再发起第三次请求；旧执行者结果由 fencing 拒绝。来源回退按来源分别应用该上限。该机制是带 fencing 的单飞尽力保证，不宣称外部行情调用绝对恰好一次。

**发布与令牌：**视图先写入 generation 隔离的临时键并设置 TTL，校验证券/指数报价覆盖和数据时间后，再以原子操作切换活动 generation。S1 API 必须返回 `dataset_version,pool_version,pool_hash,pool_count,rule_version,time_context_id`。`read_token` 绑定 `dataset_id + pool_version + pool_hash + rule_version + time_context_id + realtime_view_id + query`；`ReadContext`、游标、Redis 视图及结果缓存均携带同一 pool 版本。即使同一 dataset 下出现新 pool，既有 token 仍只能读取原 pool；pool 版本变化必须新建视图和 token，不可复用或混合。同令牌、同视图有效期内的单股与预测读取一致。同上下文后来生成新视图时返回替换 token，客户端整体替换上下文，不能只替换部分卡片。若令牌绑定的 Redis 视图已过期，读接口返回 `409 context_expired` 并要求重取 S1；不得静默切到活动新 generation 或拼接旧新卡片。未变结果可以在新 generation 引用上一有效结果，但引用链最多一层；不得复制全市场结果或无限延长旧结果 TTL。

**行情配对与展示：**最大股票/指数报价时间偏差由 `source_policy` 按行情源与交易阶段配置，必须在 P1 依据已验证源延迟冻结具体秒数并以边界夹具验收，禁止模块各自写常量。超过最大偏差、任一报价过新鲜期限或指数映射不确定时，该证券依赖该配对的盘中偏离不可用，不得生成触及/安全结论；不受该报价影响的已发布收盘指标仍可独立显示。视图和 API 按证券返回 `quote_as_of`、指数 `index_quote_as_of`、二者 skew、source 与 `freshness`。5 秒内可标 fresh；5–30 秒只允许作为 stale 的最后成功结果展示，不可用于当前触及/安全结论；超过 30 秒按 unavailable 处理。

**Redis 不可达：**后端不使用实例本地实时副本，也不声称读取了 Redis 共享视图。S1 和已发布收盘数据查询继续使用 MySQL；S2 可将幂等刷新写入 MySQL `job_run` 并有界排队等待 Redis 恢复，无法持久排队时返回 `503 realtime_unavailable`。任何情况下都不得绕过 Redis 租约逐实例回源。排队超过配置上限则任务失败并返回明确原因。前端可保留已渲染的原结果，显示其真实 `quote_as_of` 和“更新中/实时行情不可用”，该客户端保留行为不把旧行情重新判为当前触及或安全。Redis 恢复后只由仍在期限内的持久任务或新刷新取得行情，不复活过期视图。

**实例配置与淘汰：**使用单独的 Redis key namespace，不执行 `FLUSHDB/FLUSHALL`。启用短租约和限流前，必须核验腾讯云实例 `maxmemory`、内存余量、`maxmemory-policy` 和 `evicted_keys`。本方案要求 `noeviction` 或经专项验证可保证协调键不被淘汰的等效策略；内存写满时安全失败并告警。若腾讯云实例无法满足该门槛，新版本不得开放跨实例盘中实时刷新，不得仅把 Redis 当缓存后宣称已解决并发。上线设置内存水位告警，且验证峰值视图/租约容量。

**来源限流：**只有在实时供应商的实际配额/请求约束需要时，启用 Redis 共享限流器；以来源维度使用原子令牌桶脚本，限流发生时等待或返回有界排队，不对每个实例各自独立限流。供应商调用仍按既定批次上限和超时执行，不能为压测直接请求公开行情源。

发布顺序的唯一解释：Redis generation 对象先写入只是暂存；必须先由 MySQL 事务/CAS 授权 `active_realtime_view_id + generation`，之后才可更新 Redis 活动提示。Redis 活动提示是可丢失的加速引用，任何读取均以 MySQL 授权的 `view_id/generation` 为准，禁止因提示领先、滞后或丢失而静默换视图。

### 10.3 并发、配额与失败

按上面的 Redis 租约合并名单刷新轮次和证券级报价请求。全局源限流通过共享令牌桶执行；未配置或未通过实测的源不得假定一个固定 QPS。API 只提交幂等命令/有界实时请求，不运行全市场重建或 OCR。

S2 超过同步预算时可返回 202 + `job_id,retry_after_ms`；job 在 MySQL 持久队列中按唯一 `dedupe_key` 合并，Redis 短租约只负责行情请求/视图的跨实例单飞，不替代 MySQL 的任务所有权、重试与 fencing。响应固定区分 `refresh_state=ready|queued|failed` 与 `result_state=fresh|stale|unavailable`，并返回 `job_id`、`retry_after_ms`、可选 `read_token/realtime_view_id`、`quote_as_of` 和 `fresh_until/stale_until`。queued 时仅当服务能读取并验证有效 Redis 视图才附带结果；否则不返回实时卡片，前端保留已渲染内容并标“更新中/实时行情暂不可用”，不可把上一收盘结果标为实时。轮询带 jitter、退避和次数/时限上限，离页取消；若提交刷新时绑定的 `read_token/context` 已过期，POST 返回 `409 context_expired`，前端重取 S1；已排队 job 的 GET 轮询只要 job 存在且查询本身成功，一律返回 HTTP 200，即使实时视图/token 已过期，也在 body 中保留 `job_state`、将 `result_state=unavailable` 并附 `CONTEXT_EXPIRED`，客户端丢弃过期视图并整体重取 S1。名单刷新批量数和 freshness TTL 在 P1 用实际数据源配额测试冻结；不得附带全市场刷新或次日实时重算。

**Job 状态机与轮询终止：**`queued|running|retry_wait` 为非终态；`succeeded|failed|cancelled|expired` 为终态。`GET /jobs/{id}` 终态返回与 S2 相同的 `refresh_state/result_state`、结果引用/读取令牌、报价时间及原因码。成功任务若生成的视图在读取前已过期，仍报告 job 成功、结果过期并要求重取 S1；不能无限续排。客户端遇终态停止轮询；非终态使用有上限的退避和最大等待时间，超限后停止并呈现失败/暂不可用。

**结果状态行为：**`fresh` 可参与当前计算结论；`stale` 仅能作为附有真实 `quote_as_of` 的最近已成功画面展示，不能用于当前触及/安全判定；`queued` 表示当前刷新进行中，客户端可保留已有画面并标“更新中”；`failed`/`unavailable` 显示明确原因和最后成功时间（若有），不得伪装为安全或实时成功。HTTP 状态、业务码、任务态与结果态按此契约实现。

## 11. 数据模型与存储约束

### 11.1 建模原则

MySQL 是结构化事实、修订、结果和任务的真值；PDF 仅按 §15 使用实例临时目录缓存。派生向量、读模型和 Redis 可重建。物理表按完整性职责划分，初期不因评审列出较多逻辑实体就全部创建；同语义可用 `kind` 区分，索引与访问形态确有差异时再拆。

下面是首期逻辑模型，DDL 必须包含在独立迁移中，由 MySQL 实际版本验证 CHECK、JSON 和索引行为。业务字段不可变，生命周期与审计可追加；并非所有表都每次重建复制全部历史。

### 11.2 表、主字段与约束

| 表 | 必填字段/关键内容 | 主键、唯一键与主要索引 |
| --- | --- | --- |
| `security_revision` | security_id、revision_id、代码/名称/交易所/板块/属性、生效区间、source_ref | PK revision_id；索引 security_id+valid_from；有效区间冲突由迁移/门禁拒绝 |
| `mapping_revision` | 证券/板块→基准指数、价格政策、valid_from/to、来源 | PK revision_id；索引 scope+valid_from；不假造缺失的上市以来历史 |
| `calendar_revision` | exchange、trade_date、is_open、session_segments、revision_id | UNIQUE exchange+trade_date+revision_id；输入清单固定修订 |
| `market_bar_revision` | instrument_id、kind(stock/index)、trade_date、close、pre_close、pct_chg、adj_factor、source、hash、revision_id | UNIQUE instrument_id+trade_date+source+content_hash；索引 instrument_id+trade_date；最新指向可变但数据集不读它 |
| `trading_event_revision` | security_id、停/复牌/终止/退市整理、开始结束、原因、证据、确认度 | PK revision_id；索引 security_id+effective_date；原因未知可存但不伪造 |
| `rule_bundle` | rule_version、content_hash、schema、配置文本、basis清单 | UNIQUE rule_version；哈希变化禁止覆盖同号；发布需审核状态 |
| `rule_evidence` | evidence_id、rule_version、rule_key、source_type(`product_policy`)、policy_basis、source_url(可选)、fixture_refs、content_hash、effective_from/to、applicability_scope、review_status、reviewed_by、reviewed_at | PK evidence_id；规则包以 evidence_id 引用；产品批准规则与补充材料分开保存；缺失、冲突或未审核的必需证据阻断对应规则包发布 |
| `return_policy_revision` | policy_id、rule_version、board_scope、event_type、stock_return_method、index_return_method、comparison_base、suspension_resume_policy、ex_right_policy、valid_from/to、evidence_ids、review_status、content_hash | PK policy_id；索引 rule_version+board_scope+valid_from；每个有效规则包引用唯一适用政策版本；缺收益口径、基点或证据时阻断受影响规则包，不用运行时默认值 |
| `source_batch` | source、kind、scope、watermark、分页、完整状态、输入引用 | PK batch_id；UNIQUE source+request_fingerprint+run_revision；失败状态持久保存 |
| `announcement_revision` | source_ann_id、publisher、channel、披露时刻、URL、正文证据、正文哈希、解析版本 | PK revision_id；UNIQUE source+source_ann_id+content_hash；正文证据不可覆盖 |
| `fact_revision` | fact_id、revision_id、security_id、type、区间、方向、期限、identity_hash、状态、supersedes、记录时刻 | PK revision_id；规范身份索引用于普通事实；索引 security_id+fact_end_date；字段不全不得确认 |
| `fact_evidence` | fact_revision_id、announcement_revision_id、页码/文本区间、证据哈希 | UNIQUE fact_revision_id+announcement_revision_id+locator；无 PDF 的正式 HTML 也可有证据 |
| `input_manifest` | manifest_id、root_hash、输入修订清单引用、源批次、knowledge_cutoff | PK manifest_id；封存后不可更改；通过 MySQL 记录的源批次和修订引用重放，不引入外部对象存储依赖 |
| `dataset` | id、version、规则/代码/解析/清单引用、业务日期、生命周期、result_hash、counts、验证摘要 | PK id；UNIQUE version；无“同日只能有一份曾发布”约束 |
| `dataset_fact_ref` | dataset_id、fact_revision_id、用途、确认/排除结果 | UNIQUE dataset_id+fact_revision_id+purpose；固定事实选择视图 |
| `same_direction_event` | dataset_id、security_id、fact_id、fact_revision_id、end_date、direction(`up|down|null`)、`direction_resolution_status(resolved|pending_direction)`、direction_basis、direction_reason_codes[]、market_check_status、calculation_input_hash（nullable） | UNIQUE dataset_id+fact_id；索引 dataset_id+security_id+end_date；公告确认事实立即建立唯一事件记录，并按 `effective_date` 建立重置边界（边界到期前不应用重置）；方向暂不可得时记 `pending_direction`，不猜方向、不重复建事件；窗口内待定事件使涨跌两侧 `exact_count=null`，两侧 `confirmed_count` 仅为已解析事件下界；方向解析后在新数据集按同一 fact_id 归入唯一方向，不重复建事实事件；市场核验状态/哈希独立可空，不是计次/重置门槛 |
| `reset_boundary` | `reset_boundary_id,dataset_id,security_id,metric_keys_canonical,boundary_key,trigger_fact_revision_ids,source_evidence_ids,policy_id,policy_version,trigger_trading_date,source_published_at,effective_date,effective_date_basis,first_return_date,baseline_date,baseline_price_ref,baseline_index_ref,clear_events_before,market_check_status,market_input_hash,decision_status,supersedes_boundary_id,decision_hash,recorded_at` | `metric_keys_canonical` 为排序去重后的指标键 JSON；`boundary_key=SHA256(dataset_id|security_id|metric_keys_canonical|effective_date|sorted(trigger_fact_revision_ids)|clear_events_before|policy_version)`；UNIQUE dataset_id+security_id+boundary_key；事实修订/证据修订可追溯；同日多事实按 effective_date、fact_revision_id 稳定排序并合并边界，保留所有来源；更正/撤回仅在新 dataset 重放 |
| `metric_result` | dataset_id、security_id、rule_key、role、target_date、direction、value、threshold、状态/可用性、reasons、依据 | UNIQUE dataset_id+security_id+rule_key+role+direction；索引 dataset_id+role+rule_key+status |
| `stock_result` | dataset_id、security_id、状态、当前/次日摘要、标签、文案、监控摘要、模拟基础引用 | UNIQUE dataset_id+security_id；派生只读，可由输入与结果重建 |
| `dataset_pointer` | env/channel、dataset_id、generation、previous_id、changed_at | PK env+channel；发布及回滚 generation 单调加一 |
| `job_run` | job_id、kind、dedupe_key、scope、stage、state、owner、fence、lease_until、attempt、next_retry、error | UNIQUE kind+dedupe_key；索引 state+next_retry；租约可独立表但同一协议 |
| `realtime_scope_fence` | `view_scope_key`、monotonic_fence、owner_job_id、active_realtime_view_id、active_generation、完整 view context、updated_at | PK `view_scope_key=SHA256(dataset_id|pool_version|pool_hash|rule_hash|time_context_id|target_date|role|market_phase|scope_hash|source_policy|quote_bucket)`；与行情刷新去重 key 分离；每个完整读取上下文独立递增 fence，视图发布用 CAS 校验传入 fence 等于当前 fence 且上下文逐字段匹配 |
| `idempotency_record` | scope、idempotency_key(entry_id/request_id)、canonical_request_fingerprint、first_context_snapshot/job_id/response_ref、state、created_at、expires_at | UNIQUE scope+idempotency_key；同 key 同 fingerprint 重放原结果；不同 fingerprint 返回 409；至少保留 24 小时且覆盖最大排队/重试期 |
| `quality_result` | dataset/build、gate_id、gate_config_hash、scope、status、实测值、阈值、artifact、复核记录 | UNIQUE dataset_id+validation_run_id+gate_id；结果全部落账，不在回滚事务里丢弃 |
| `outbox_event` | event_id、type、payload_ref、created_at、delivered_at、attempt | PK event_id；消费者幂等 |
| `audit_event` | action、actor_role、目标修订、原因、时间、前后hash、外部操作引用 | 追加写；覆盖发布、回滚、人工核验、清理、迁移 |
| `realtime_view` / `realtime_result` | 基线、共享临时视图、覆盖范围、报价refs、指标、TTL、父视图 | view PK；result UNIQUE view_id+security_id；短期保留，不混入收盘结果 |

搜索索引、告警通知表、归档分区、向量缓存可在实际需要时增加，不作为额外业务模块。`quality_result`、`audit_event`、`outbox_event` 不能为省表而丢失各自事务与查询语义。

### 11.3 数据集输入清单

清单必须列出证券/指数每条所选行情修订、证券/映射/日历修订、交易事件修订、公告和事实修订、覆盖批次、规则包、代码与解析器集合、窗口/缺因/展示版本。仅保存一个“源快照 hash”但无法找回对应修订，不构成可重放。

缺少历史时先回填计算所需全部依赖，再按修订封存。不要求一开始回填三年才能灰度；近期 20 交易日双跑也要额外取每个终点之前至少最长窗口、计次及上一个可靠重置检查点的输入。删除原始修订前检查保留中的数据集引用，禁止只保留结果却删掉唯一复算输入。

### 11.4 库约束与应用校验

正式数据：`unavailable ⇒ value不可伪造且reasons非空`，安全/未触发不能带阻断本结论的缺因；提示类 notices 不受此限制。`regulatory=null ⇒ availability=unavailable 且 reasons非空`。普通公告事实确认时先创建唯一同向事件和重置边界记录；到达边界的 `effective_date` 才应用 3 日重置；方向枚举为 `up|down|null`，解析状态为 `resolved|pending_direction`。正文未明确方向且精确区间行情暂缺时，方向字段为 null，原因码非空。只要待定事件落在目标 10 日窗口内，涨、跌两侧 `exact_count` 都为 null；两侧 `confirmed_count` 分别统计已解析事件，作为下界，不含待定事件。若任一方向下界已达到阈值，可判该方向至少触发；否则待定事件可能改变任一侧结果，10 日同向不可判安全。方向确定后按原 fact_id 在新数据集归入唯一对应方向 +1，不新增第二事件；禁止将待定方向事件计为上涨或下跌。已确定方向的正式同向事件必须有方向依据；行情核验字段可为 `matched|mismatch|missing|pending`，不得作为事件写入、计次或重置约束。无法精确给出窗口次数时 `exact_count=null`，不得以 0 替代。八位日期校验真日期；正式发布的普通风险、严重监管和退市整理事实 `valid_from/valid_to` 必填，开放式/未知结束日不得用 NULL 伪装为有效事实，必须进入事实待核验并阻止该状态发布。NULL 只用于确实没有事实有效期概念的事件或独立附件保留字段，不用于已发布状态事实；日期区间统一采用闭区间 `[valid_from,valid_to]`。

版本号长度按语义分列并留足边界，规则号不塞入生成器长描述；CI 检查最大长度和兼容旧版。已有 MySQL 历史结构不在应用启动中“自动修一下”；所有变更先迁移再发布。

首期不启用表分区或只读副本作为默认。固定数据集查询走主库/满足读后写一致性的路径；未来启用副本时需确认数据和指针同一复制位置，不能只把指针读主库而明细读未追上的副本。

## 12. 接口详细设计

### 12.1 传输约定

新协议使用 `/api/v2/`，响应字段统一 snake_case，与数据库和内部类型一致；现有 camelCase 响应由兼容适配器转换，禁止在一份新协议中混用。保持原 `{code,message,data}` 并增加 `trace_id`；不将未处理异常或供应商原始响应返回客户端。

鉴权边界固定如下：`read_token` 是服务端签名的短期不透明令牌，至少绑定 `dataset_id、pool_version/hash、rule_hash、time_context_id、target_date、role、query scope、realtime_view_id` 和 `expires_at`，服务端校验签名、受众、scope、撤销边界和最早过期时间；小程序不持有签名密钥。公告 PDF 使用服务端签发的短期 HMAC 阅读凭证，绑定受信来源 URL、事实类型和监管结束日；有效期最多 5 分钟，签名密钥 `MONITOR_PDF_TOKEN_SECRET` 独立于 S1 密钥并至少 32 字节。各 CloudRun 实例验签后才可按需重取来源，拒绝客户端 URL 透传；到期或监管结束日已过拒绝服务。签名密钥采用版本化 key-id、双 key 短期重叠轮换和旧 key 撤销记录；轮换不延长既有令牌有效期。发布、重建、人工核验、清理和健康详情等内部写/敏感接口只接受云托管服务身份（短期服务令牌或等效 mTLS/工作负载身份），拒绝小程序 token、匿名请求和跨服务伪造，并记录调用主体、scope、结果和 trace_id。

领域缺数可以 HTTP 200，但响应内明确不可用和缺因；网络/参数/权限/服务故障使用相应非 2xx，不能一律返回 code=0。状态正常的“次日暂不开放”是业务访问限制，不是数据源失败。公开读取无需登录；重建、发布、人工核验和清理命令必须是服务身份鉴权的内部接口，不放入小程序。

| 方法与路径 | 入参 | 主要输出/限制 |
| --- | --- | --- |
| `POST /api/v2/context` | page、role、security_id（单股可选）、`entry_id`（页面进入稳定请求 ID）、已持有上下文 | S1 context：`read_token:{value,expires_at}`、dataset、`dataset_version,pool_version,pool_hash,pool_count`、time_context、entry_id；同 entry_id 重试返回同一 context，不触发全市场重算或盘中改写名单 |
| `GET /api/v2/stocks/search` | q、limit≤20 | 身份、代码、名称、板块；输入限长，参数化查询；不记录 q |
| `GET /api/v2/stocks/{security_id}` | read_token | 状态卡、四项 current/next、模拟基础；交易上下文无次日结果时明确不可用，盘中 S2 不产生 next |
| `GET /api/v2/monitor` | read_token、type、cursor、limit≤50 | 有效事实列表、期限和状态、分页；按目标日期筛选 |
| `GET /api/v2/predictions` | read_token、role=current/next、cursor、limit≤50 | 已发布名单或同基线名单实时结果、排除汇总、范围完整性、next_access |
| `POST /api/v2/realtime/refresh` | `read_token.value`、scope=prediction/stock、security_id（stock）、HTTP `Idempotency-Key`/`request_id` | S2：ready/queued/failed、可选替换 read_token、当前结果/任务；股票范围由绑定的 pool_version 名单或单股决定，不接受任意全市场名单；超时重试复用 request_id；next 禁止 |
| `GET /api/v2/jobs/{public_job_id}` | 只读限速令牌 | 进度、阶段、retry_after_ms、已生成的读取上下文；无内部异常细节 |
| `GET /api/v2/rules` | read_token 或明确 rule_version | 同数据集规则说明、阈值/来源/产品口径、展示政策、模拟兼容能力 |
| `GET /api/v2/facts/{fact_id}` | read_token | 选定事实修订、来源证据、有效期、更正关联、附件能力 |
| `GET /api/monitor/announcement?key=...`（受控兼容下载端点） | 小程序 HTTPS 合法下载域名内的受控入口；key 只映射当前快照中有效且有可信 PDF 来源记录的公告，不接受任意外部 URL；新 v2 查询契约可返回同一受控端点或等价版本化路径，但不得绕过授权 | 缓存命中时 `200 application/pdf`；本机未命中时按已校验来源安全重取并校验后返回 `200`；未知/失效/来源不可信/重取失败返回明确错误；不重定向浏览器、不下发源站凭据 |
| `GET /api/v2/dataset-status` | 可选 read_token | 发布指针 generation、数据日期、构建/失败说明；不抓上游 |
| `GET /health/live` | 无 | 进程存活；不查询 Tushare/MySQL |
| `GET /health/ready` | 内部 | Schema/配置/最低读取能力，受限时超时；外部实时源故障不重启健康 API |
| `GET /health` | 运维鉴权 | build、schema、pointer、任务/门禁/数据质量汇总，敏感字段脱敏 |

分页游标签名并绑定 dataset、实时视图、日期角色、筛选和顺序，换版本不能继续旧游标；返回 409 并要求从第一页读取。旧 API 参数 `scope=today|next_day` 只在适配器映射到 current/next。旧 POST 不用 HTTP 302 转发，服务端调用同一应用方法，保留幂等与方法语义。

### 12.2 公共响应示例

下面仅为契约样例，不是本轮真实行情或发布状态。时间戳的连字符是 RFC3339 格式要求，所有业务日期仍为八位。

```json
{
  "code": 0,
  "message": "ok",
  "trace_id": "example-trace",
  "data": {
    "api_schema_version": "2.0",
    "read_token": {
      "value": "example-signed-context",
      "expires_at": "2026-10-28T16:30:00.000+08:00"
    },
    "dataset": {
      "dataset_version": "20261028-01",
      "pointer_generation": 18,
      "rule_version": "example-rule-1",
      "rule_hash": "example-sha256",
      "pool_version": "20261028-POOL-01",
      "pool_hash": "example-pool-sha256",
      "pool_count": 37,
      "close_as_of_date": "20261028",
      "fact_as_of_date": "20261028",
      "published_at": "2026-10-28T16:20:00.000+08:00",
      "freshness": "fresh"
    },
    "time_context": {
      "id": "example-time-context",
      "business_date": "20261028",
      "next_business_date": "20261029",
      "server_time": "2026-10-28T16:25:00.000+08:00",
      "next_switch_at": "2026-10-29T09:00:00.000+08:00",
      "expires_at": "2026-10-28T16:30:00.000+08:00"
    },
    "session": {
      "intraday_eligible": false,
      "reason_code": "AFTER_CLOSE",
      "next_prediction_open": true
    },
    "quote_layer": {
      "kind": "close",
      "realtime_view_id": null,
      "quote_date": "20261028",
      "quote_as_of": null,
      "source": "tushare_daily",
      "source_scope": "formal_close_only",
      "notices": []
    }
  }
}
```

样例故意将无法证明的收盘具体行情时刻置 null；数据集有真实 published_at 不代表能捏造报价时刻。源若有准确时间就填写实际时间。

S1 是一个逻辑阶段、可由多个 HTTP 请求组成：`POST /context` 固定一次 pointer 并返回 context；随后以该 `read_token.value` 调 `GET /predictions?role=current&limit=20` 取得首屏。列表响应必须回显同一 `dataset_version,pool_version,pool_hash,pool_count,rule_version,time_context_id`。只有 context 与首屏列表均成功且版本回显相同，S1 才算成功并允许交易时段启动 S2；任一失败都不启动 S2。S2 ready 后用响应的新 context/token 整体重取同一名单首屏，不得只替换各卡实时字段。

### 12.3 单股和指标对象

`StockDetail` 必填：`identity, quote, trade_state, state_tags[], regulatory, confirmed_facts[], status_card, next_day_conclusion, warnings[4], simulation, notices[]`。`warnings` 顺序固定为四项，且每项包含 current/next 和上下方向对象。模拟不可用也保留卡片和缺因，不用缺字段代替错误。

```json
{
  "rule_key": "deviation_3d",
  "role": "current",
  "target_date": "20261028",
  "direction": "up",
  "level": "ordinary",
  "availability": "available",
  "status": "triggered",
  "value": "0.21",
  "threshold": "0.2",
  "comparator": ">=",
  "window": {"start_date": "20261026", "end_date": "20261028", "actual_days": 3},
  "confirmation": {
    "status": "pending",
    "fact_revision_ids": [],
    "reasons": [{"code": "ANNOUNCEMENT_BATCH_INCOMPLETE", "text": "公告核验尚未完成"}]
  },
  "reasons": [],
  "presentation": {
    "value_text": "+21.00%",
    "status_text": "3 日偏离达到计算阈值",
    "tone": "warning",
    "ring": {"progress_pct": 100, "unit": "percent", "text": "+21.00%"}
  }
}
```

这是单个方向结果的片段，不是完整四卡响应。其计算已达标，不因公告确认待核验而把已知 21% 清空或显示安全；监管事实确认依赖 `confirmation`，并无矛盾。次数对象另含 `confirmed_count, exact_count, projected_count, count_limit, counted_fact_refs, requires_announcement_confirmation`。

`status_card` 给定 `variant, badge, evidence_date, lines[], notice, tone`；每行文案由服务端基于固定结果生成，前端不拼监管结论。`next_day_conclusion` 给定 code、tone、target_date、rule_refs、reasons，不能由页面拿预测列表非空反推。

`read_token` 的过期时间是独立字段，不能由 `time_context.expires_at` 代替。刷新接口和 GET job 的 `read_token` 均使用 `{value,expires_at}`；请求头/参数发送其 `value`，客户端在 `expires_at` 到达时整体重新取 S1。

### 12.4 S2 状态组合

刷新命令 `POST /realtime/refresh` 与 job 查询 `GET /jobs/{id}` 按接口分别遵循下表；其他状态组合按协议错误拒收，不得降级为成功。POST 即时执行失败返回 502/503；超过同步预算或可排队条件返回 202。只要 job 存在且查询本身成功，GET 恒返回 HTTP 200 并在 body 表达业务终态；鉴权、参数、服务不可达和 job 不存在等查询自身失败才返回非 2xx：

| 接口 / HTTP | `refresh_state` | `result_state` | 必需返回 | 含义 |
| --- | --- | --- | --- | --- |
| POST / 200 | `ready` | `fresh` | `read_token,realtime_view_id,quote_as_of,results` | 本轮刷新成功；结果可用于当前盘中判断 |
| POST / 202 | `queued` | `stale` | `job_id,retry_after_ms,read_token,realtime_view_id,quote_as_of,stale_until,results` | 任务排队，同时返回可验证的已有视图与结果；旧结果仅展示，不能参与当前触发/安全判断 |
| POST / 202 | `queued` | `unavailable` | `job_id,retry_after_ms,reasons` | 任务排队，暂无可用实时视图 |
| POST / 502/503 | `failed` | `stale` | `job_state=failed,read_token,realtime_view_id,reasons,quote_as_of,stale_until,results` | 即时刷新失败；只可展示最近成功视图 |
| POST / 502/503 | `failed` | `unavailable` | `job_state=failed,reasons` | 即时刷新失败且无可展示实时视图 |
| GET job / 200 | `failed` | `stale` | `job_state=failed,read_token,realtime_view_id,reasons,quote_as_of,stale_until,results` | job 查询成功；业务任务失败，只可展示最近成功视图 |
| GET job / 200 | `failed` | `unavailable` | `job_state=failed,reasons` | job 查询成功；业务任务失败且无可展示实时视图 |
| GET job / 200 | `ready` | `unavailable` | `job_state=succeeded,reasons=[CONTEXT_EXPIRED]` | job 曾成功但结果已过期；查询成功，客户端整体重取 S1，不返回过期结果 |
| POST / 409 | 不适用 | `unavailable` | `code=context_expired` | 请求绑定的令牌/视图/时间上下文已失效；丢弃旧上下文并重新取 S1，不拼接结果 |

禁止 `ready+stale`、POST `ready+unavailable`、`queued+fresh`、`failed+fresh`。GET job 的 `job_state` 必须为终态 `succeeded|failed|cancelled|expired`；若 succeeded 的结果在查询时已过期，仍 HTTP 200 返回 `result_state=unavailable` 与 `CONTEXT_EXPIRED`，调用方整体重取 S1。409 仅用于带过期上下文的刷新/数据读取请求，不用于已成功读取 job resource。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "refresh_state": "ready",
    "result_state": "fresh",
    "job_state": "succeeded",
    "read_token": {"value": "replacement-context", "expires_at": "2026-10-28T10:00:30.000+08:00"},
    "realtime_view_id": "rtv-42",
    "quote_as_of": "2026-10-28T09:31:02.000+08:00",
    "fresh_until": "2026-10-28T09:31:07.000+08:00",
    "results": []
  }
}
```

`202 queued/stale` 必须同时含 job 字段和可验证旧视图的真实时间及结果；`202 queued/unavailable` 不得带实时结果。`failed/stale` 必须带当前仍可访问的旧视图 token/id、展示结果和真实 quote time，明确标 stale；失败不得把收盘值或旧行情伪装成 fresh。`failed/unavailable` 的 `results` 必须省略。S2 的 202 不是刷新成功，测试报告单独统计任务最终完成和端到端时延。

### 12.5 错误和重试

| HTTP/code | 语义 | 客户端 |
| --- | --- | --- |
| 400/40001 | 参数、证券代码或 role 非法 | 修正输入；不重试 |
| 404/40401 | 身份/证据不存在 | 明确未找到；不是安全 |
| 409/40901 | 上下文失效、指针更新或旧游标 | 重取 S1，一次自动重试，避免循环 |
| 409/40902 | `context_expired`：read_token 绑定的实时视图已过期 | 丢弃旧实时覆盖层，重取 S1；不得拼接旧新卡片 |
| 410/41001 | 附件已按策略清理 | 显示已清理，提供可用原文能力 |
| 429/42901 | 刷新限流 | 尊重 Retry-After，保留已有结果 |
| 503/50301 | 尚无可读数据集或系统暂不可用 | 有缓存则标过期，否则错误态 |
| 503/50302 | `realtime_unavailable`：Redis 不可达且无可执行的持久排队操作 | 保留已渲染内容及原始时间，明确显示实时不可用；不改成安全 |
| 200/code=0 + unavailable | 某指标关键数据缺失 | 展示原因；不能替换成 0 |
| 202/code=0 + queued | 名单实时刷新正在进行 | 带上限退避，不重复提交新任务 |

## 13. 任务编排、幂等与故障恢复

### 13.1 同镜像三个角色

`APP_ROLE=api` 只查询和接收有界命令；`worker` 运行持久化作业；`migrate` 一次性执行迁移，退出。开发和生产用相同业务代码，配置和密钥隔离。worker 初期一个活跃执行者，仍必须使用数据库租约，因为重启、交接和失联旧进程会并存。

任务语义是**至少一次执行 + 幂等提交**，不承诺外部调用恰好一次。`job_run` 保存 stage、attempt、输入hash、产物hash、错误、下次重试和 owner/fence。幂等键按任务类型设计，不只用“今天跑过”：同日新公告修订或新规则应产生新的构建键。

### 13.2 租约和 fencing

Redis 短租约只负责 API/worker 对同一实时报价或实时视图的短期请求合并，生命周期以一次盘中刷新为限；它不决定持久任务是否完成，也不替代数据库租约。MySQL `job_run` 的 owner/fence 负责持久任务唯一所有权、重试和旧任务拒写；`realtime_scope_fence` 为每个完整视图上下文提供跨 Redis 切主仍单调的持久 fence 及 active view 授权。去重 `scope_hash` 与持久 view fence key 不同：后者至少包含 dataset、pool version/hash、rule hash、time_context、目标日期、role、market phase、scope 成员哈希、source policy 和 quote bucket，避免相同成员清单但不同读取上下文争用同一活动槽位。发布时先将完整不可变视图写入 Redis generation 暂存键，再在 MySQL 事务中锁定精确 view scope fence，确认 fence/owner/job/完整上下文逐字段匹配后 CAS 更新该上下文的 `active_realtime_view_id + generation`；旧任务或旧 fence 必须拒写。Redis 活动指针仅为加速引用，读取必须匹配 MySQL 授权的精确 view id/generation。Redis 故障或 failover 后引用对象丢失/不匹配时返回 queued/unavailable/context_expired，不回退实例本地副本或逐实例直连上游。任务即使重复投递，也由 `dedupe_key` 和正式结果唯一约束吸收。

数据库 UTC 时间为租约准绳。领取时递增 fence，续约仅允许同 owner/fence；过期后新 worker 获得更大 fence。每一批候选写入必须在同事务中锁定/校验 job 和 dataset：租约未过期、fence 相同、candidate 仍 building。实时视图活动指针还须校验对应 scope 的持久 fence。失租任务即使网络恢复，也不能继续落可见结果或发布。

不能用“写前查一次 token、随后无条件写入”实现保护，那存在检查和写入之间的竞争窗口。封存后 candidate 禁止再 upsert；重试如不能证明输入与已有行相同，则创建新构建修订，不覆盖封存数据。原始输入幂等写入与构建结果可见性分开。

### 13.3 作业清单

| 作业 | 触发/前置 | 产出与失败处理 |
| --- | --- | --- |
| `reference_sync` | 每日/日历变更/启动补缺 | 证券、日历、映射修订；已有完整覆盖可继续用，过期或关键缺口阻断依赖 |
| `daily_ingest` | 正式日频可取后，多轮重试 | 按交易日批量拉取、校验分页/数量；只对确有缺口个股补采 |
| `announcement_collect` | 盘后、夜间补采及增量 | 按类型范围记录批次，不将采集异常当无事实 |
| `announcement_parse` | 下载产物到达 | 文本/OCR及结构化事实；有界 CPU 池，超时重试，死信可补跑 |
| `dataset_build` | 输入齐备/事实修订/规则换版 | 固定输入后计算、匹配、按生效顺序重放；每项独立缺因 |
| `dataset_validate_publish` | 构建封存 | 适用门禁完成后 CAS 发布；失败保留旧指针 |
| `prediction_realtime_refresh` | 首开/主动刷新、有效交易时段 | 仅当日预测名单和对应指数；不刷新全市场、不生成 next |
| `single_realtime_refresh` | 单股首开/主动刷新 | 当前选股；共享同版结果，失败保留时间明确的最后结果 |
| `catchup_scan` | 定期检查任务计划与成功记录 | 补跑漏执行/中断/死信；不靠进程记忆判断今天做完 |
| `retention_cleanup` | 每日 | PDF/临时视图/无引用结果清理，审计留痕 |
| `consistency_audit` | 发布后/定期 | 单股-预测-监控跨页同版、不变式、证据与对象可访问性 |

准确采集时刻从现有已验证数据到达情况迁移，参考源官方发布时间但不把估计时刻当保证；夜间补采必须覆盖收盘之后的新公告。任务 DAG 允许行情与公告采集并行；发布必须使用固定输入，不能在计算中途读到新事实又只更新一半股票。

### 13.4 失败矩阵

| 故障 | 行为 |
| --- | --- |
| 全局日历缺失 | 不能猜自然工作日；不构建依赖的新集，旧结果带过期说明 |
| 个股/单指数缺数据 | 仅相关指标不可用，完整性分母保留该证券；超质量阈值才阻断整体 |
| 公告必要范围失败且无法界定影响个股 | 关键覆盖门禁失败，不确认无事实 |
| 可定位正文失败 | 该股确认/计次待核验，其他独立已知结果保留；触发补正文 |
| 构建进程崩溃/失租 | 新 worker 重试；未封存结果不可读，旧 worker 被 fence 拒写 |
| DB 暂不可用 | 查询降级为有日期的客户端缓存或错误；不回内存空成功 |
| Redis 不可用 | 正式查询继续从 MySQL 读取。后端不得用进程本地行情副本，也不得各实例绕过 Redis 直连上游；实时刷新仅可进入 MySQL 持久队列等待恢复或返回 `503 realtime_unavailable`。小程序已显示的旧画面可保留为 stale，带原始时间且不参与当前指标触及/安全结论 |
| 盘中行情源临时失败 | 沿用已配置的实时源回退顺序并记录实际源和失败原因；所有来源均失败时返回 failed/unavailable，不将 Tushare 静态收盘值伪装成实时 |

## 14. 原子发布、回滚和一致性

### 14.1 构建与封存

创建 building 数据集 → 固定清单 → 分批写结果 → 校验行数/哈希/完整性 → sealing → 计算内容哈希并封存 → 执行适用门禁 → validated 或 rejected。sealed 后不能补行改值；输入补齐须创建新 candidate。所有门禁记录在发布事务外持久化，失败也保留，不被 ROLLBACK 抹掉。

### 14.2 发布事务

以下是目标伪代码，不是可以直接执行的迁移 SQL：

```text
BEGIN
  锁定 job_run，验证 owner/fence/lease_until
  锁定 candidate，验证 sealed_hash、validation_run_id、状态及兼容性
  验证全部适用 block 门禁 PASS；无记录/超时不当作 PASS
  锁定 dataset_pointer(env, channel)
  验证 generation == expected_generation
  验证 dataset_id == expected_parent_id
  验证 candidate 的父输入、目标日期和选用规则仍适合发布
  UPDATE pointer SET dataset_id=candidate_id,
                     previous_id=old_id,
                     generation=generation+1, changed_at=db_now
  记录 publication audit 和 outbox（与指针同事务）
COMMIT
```

CAS 不仅是 `SELECT FOR UPDATE`：没有 expected_generation/parent 检查，旧候选等锁结束仍可能覆盖较新数据。指针事务不包含 OCR、上游请求、全部明细写入或耗时全量校验。失败指针保持原状，状态错误可检查重试。

### 14.3 回滚与缓存

回滚验证目标数据集仍完整、规则/Schema/API 兼容、未撤销且处于允许读取范围；切回旧 dataset_id 同时 generation 继续 +1，追加原因和审计。不能按版本字符串“更小所以丢弃”拒绝合法回滚，也不能重复使用旧 generation，避免 ABA。

前端和缓存读取按 `(channel,generation,dataset_id,pool_version,pool_hash,rule_hash,time_context_id,realtime_view_id,query)`；新上下文整体替换，不能只替换头部版本号。对过时但未撤销的令牌可在短 TTL 内继续一致读取；紧急错误数据集撤销后立即拒读并要求刷新。发布失效消息通过 outbox 重试，不能因通知丢失而影响真值，指针 generation 仍是准绳。

数据集发布、后端镜像发布、小程序发布是三个不同操作。纯文案和样式变更无需重算规则数据；规则语义变更必须升级规则包及关联计算、重放、发布。后端接口变更必须兼容尚未升级的小程序，不能假设用户立即全量安装新版。

## 15. PDF 缓存、查看与清理

### 15.1 保存与打开

正文证据、解析结果、来源 URL 和公告事实按现有数据链路保存。PDF 二进制只在后端实例临时目录（默认 `/tmp/stock-abnormal/monitor-pdf`）缓存；后端通过短期 HMAC 阅读凭证校验来源、事实类型、监管结束日和缓存文件后返回 PDF。多实例缓存彼此独立：本机缓存未命中时，服务端用凭证中已签名且已校验的来源安全重取，不依赖粘性路由或其他实例本地文件。凭证有效期最多 5 分钟，`MONITOR_PDF_TOKEN_SECRET` 不足 32 字节时不生成入口。重取初始工程护栏为：单文件最大 `20 MiB`、连接超时 `5s`、读取空闲超时 `10s`、单文件总下载期限 `30s`；这些值须在真实公告样本和并发验收后写入版本化配置并冻结，超限或超时返回附件暂不可用。下载同时限制 HTTPS、主机白名单和重定向目标，校验 `%PDF` 后以临时文件原子写入；失败不接受客户端任意 URL、不影响监管事实。小程序固定流程为：请求受控 HTTPS 地址 → `wx.downloadFile` 下载到微信临时文件 → `wx.openDocument({filePath, fileType:'pdf'})`。不要求复制 URL 或跳转浏览器，不使用 Redis/COS 保存 PDF。文档不宣称尚未实现的对象哈希、对象版本或 COS 生命周期能力。

支持 PDF 的范围是普通异动公告（包括公司发布的普通异动风险提示/股票交易异常波动公告）、严重异动和退市整理公告；与本产品定义事实无关的一般风险提示公告没有 PDF 阅读入口。当前有效事实存在可信 PDF 来源记录且签名密钥已配置时，接口返回短期受控阅读入口；本机缓存不存在时下载端点按签名凭证懒重取。来源不存在或不可信、签名密钥未配置或监管期已结束时不生成入口；懒重取的来源不可用、内容不是 PDF 或校验失败时返回附件暂不可用，不返回文件内容。小程序对下载失败或无法打开给出可重试提示，并清理微信临时文件；附件不可读不改变已入账事实、计次或重置。

`pdf_available` 表示当前有效事实存在可信 PDF 来源记录；`pdfCached` 表示当前实例临时缓存是否已存在。有效事实且来源可信时接口始终返回受控公告 key，下载接口按 key 懒重取；无可信来源记录时不返回入口。正式 HTML 正文仍可作为事实证据，不要求对应 PDF 才能确认正式事实。缓存未命中或重取失败只表示阅读能力不可用，不能抹掉已确认的结构化监管事实。

### 15.2 清理条件

每次公告快照构建时清理不再属于当前有效展示期的 PDF，以及下载失败留下的 `.part` 临时文件。下载/清理按公告 key 使用同一把锁；清理先将目标文件原子 rename 为待删名并设置宽限期，已打开的文件句柄允许完成读取，避免“检查缓存→打开文件”之间的 TOCTOU。清理以当前快照中的 `monitorEndDate` 和目标日期为依据；保留事实、正文证据和来源信息，不做 PDF 持久化保全。来源仍可用且公告再次进入有效快照时，可重新按同一链路下载并校验；不能恢复时提示“公告 PDF 暂不可用”，不展示坏直链。

### 15.3 附件字段

接口返回当前快照中的受控公告 key、`pdfCached` 和可信来源状态；有效事实且来源可信时即使 `pdfCached=false` 也保留入口，由受控下载接口懒重取。无可信来源记录时不返回入口。PDF 缓存状态与计算缺因分域，不能把源不可用当作监管安全或否定事实的依据。

## 16. 缺因、提示与门禁

### 16.1 单一原因目录

以下代码是拟定 v2 canonical 目录；兼容层映射专家草案的大小写及别名。实现时由同一 schema 生成后端枚举、API schema、文案和前端兼容映射，不再维护“19 个/24 个”相互漂移的列表。扩展须版本化，未知码安全降级并上报，无需把未来无法识别的码伪装成已知安全。

| 代码组 | 细码与含义 |
| --- | --- |
| 行情 | `QUOTE_MISSING` 缺行情；`QUOTE_STALE` 截止日过旧；`QUOTE_SUSPECT` 收益口径存疑；`SOURCE_TIMEOUT` 超时 |
| 指数与身份 | `INDEX_MISSING`、`INDEX_MISALIGNED`、`MAPPING_MISSING`、`SECURITY_IDENTITY_CONFLICT`、`TRADING_STATE_UNKNOWN` |
| 日历和窗口 | `CALENDAR_UNAVAILABLE`、`HISTORY_INSUFFICIENT`、`SUSPENSION_REASON_UNKNOWN`、`RESET_BOUNDARY_UNRESOLVED` |
| 公告和事实 | `ANNOUNCEMENT_BATCH_INCOMPLETE`、`ANNOUNCEMENT_FETCH_FAILED`、`BODY_UNREADABLE`、`FACT_INTERVAL_UNMATCHED`、`FACT_DIRECTION_UNRESOLVED`、`FACT_IDENTITY_MISSING`、`SOURCE_CONFLICT` |
| 规则和结果 | `RULE_UNRESOLVED`、`RULE_VERSION_MISMATCH`、`RESULT_NOT_READY`、`UNKNOWN` |

每条 Reason 包含 `code,text,scope,dependency_ref,retryable`；多个原因保留数组和确定排序。`notices` 另含 `AFTER_CLOSE/NON_TRADING_DAY/LUNCH_BREAK/NEXT_PREDICTION_NOT_OPEN/DATASET_STALE/RATE_LIMITED/EXCLUDED_BY_POLICY` 等，它们不等于计算缺数。过期提示可以与有日期的历史安全结果并存，但不能把其当作今天安全。安全的完整依赖证明只针对所声明目标日和计算层。

### 16.2 统一 G01–G26

执行范围：D=数据集发布；R=软件/规则发布验收；O=周期巡检。发布配置明确哪些适用；不适用记录 `NOT_APPLICABLE + 原因`，不能缺项。block 不允许人工无依据豁免；warn 要有处置记录而非假装通过。

| ID | 检查及准确判定 | 范围/级别 |
| --- | --- | --- |
| G01 | 日历覆盖所有真实计算依赖、最早检查点到目标日和下一切换所需范围；不是固定前后 30 自然日 | D/R block |
| G02 | 证券 universe 与身份映射都可对账；缺映射仍留原分母并逐项不可用；无默认猜主板/沪指 | D block |
| G03 | 应有收盘行情的证券新鲜度和完整性；停牌/终止/上市例外有正式原因；非预期缺失比例预算 ≤0.5% | D block，预算需基线校准 |
| G04 | 所有用于可用结果的股票/指数日期和期间一致；缺失依赖不能生成“可用”结果 | D block |
| G05 | 必要公告覆盖范围完成；个股正文失败明确隔离；全范围未完成不得确认无事实 | D block；非关键补源失败 warn |
| G06 | 正文可读率：≥98% pass、95%–<98% warn、<95% block；分母为去重后应解析公告，0 条写明 N/A 并验证覆盖 | D/O；阈值为初始工程预算 |
| G07 | 正式事实身份、不可变修订、证据定位与内容指纹完整；HTML 不强求 PDF 字节哈希；重置可追溯 | D block |
| G08 | `event_without_fact=0`，事件与事实修订匹配；`fact_market_mismatch` 单独统计并有修正任务；转载次数增量=0；跨源同一事实只保留一个 canonical fact | D/R block |
| G09 | 同读取上下文预测与单股对应字段相等；监控来自同事实视图；预测排除不改基础四项 | D/R/O block |
| G10 | 完整规则包哈希单一；结果、说明、模拟基础一致；客户端不支持算法时禁模拟而不编造 | D/R block |
| G11 | 不可用有缺因、没有缺数造零/安全；公告未完成不阻断行情可用的 3/10/30 日结论；`regulatory=null + unavailable` 不得映射为安全；已知严重事实不被缺数抹掉；notices 与 reasons 分离 | D/R block |
| G12 | 当前事实按目标日有效期过滤；未来生效只用于对应目标日；更正不污染历史修订 | D/R block |
| G13 | 正常发布行情日期不倒退，published_at真实非空；受审计回滚另走授权命令且 generation 加一 | D block |
| G14 | 同输入+同规则+同生成器重放逐字段一致；新旧口径差异全部有分类、证据和审核，禁止未解释风险降级 | R block；无需每次发布重放二十日 |
| G15 | 有效事实且可信来源记录存在时返回当前快照受控 key；缓存命中直接服务，缓存未命中可按 key 安全懒重取并校验；到期缓存与残留临时文件被清理；无可信来源、错误或校验失败不生成死入口 | R/O 下载、打开及清理检查 |
| G16 | 监管值为 null/展示 undetermined 时必须有阻断原因；不新增业务状态；已确认事实可独立展示 | D/R block |
| G17 | UNKNOWN 非预期缺因占比：>1% block、[0.2%,1%] warn；分母为当批应判定方向结果数，零分母失败/N/A明确 | D/O，工程预算可版本化校准 |
| G18 | 缺因/提示文案、颜色、百分号、不可用及未知 code 全覆盖组件回归；无建议性交易文案 | R block |
| G19 | 单股下拉关闭；预测下拉若存在只 S1；盘中只当日预测名单/所选单股、next 不回源；首开不依赖永久标记 | R block |
| G20 | 每个适用门禁记录输入哈希、配置哈希、运行号、实测值与结果；超时、漏项、报告hash不符不得发布 | D/R block |
| G21 | 五类当前基准映射、规则来源和生效区间完整；规则证据缺失/冲突阻断受影响规则包；迁移中断可恢复且 Schema/API/worker 兼容 | D/R block |
| G22 | 备份恢复至隔离库后事实、输入清单、规则引用和结果可重放；程序回滚与数据回滚分开演练；首发硬性目标为恢复点年龄 `RPO≤24h`、服务恢复耗时 `RTO≤2h`，实测值和偏差必须记录。恢复后须补采行情/公告并重放事实、重置和数据集；覆盖、事实账本及结果对账完成前受影响结果保持 unavailable/待核验，不能把旧数据集判为当前安全 | D/R block |
| G23 | PDF 受控 key 绑定当前有效事实和可信来源；HTTPS、主机白名单、重定向、超时、大小和 `%PDF` 校验全部通过 | D/R block |
| G24 | API 读取令牌、业务 scope 与公告 PDF key 授权绑定通过正反向鉴权测试；内部发布、重建、清理等写接口拒绝未授权调用 | R block |
| G25 | 数据集发布指针切换期间，三页面并发读取只得到各自完整旧版或新版上下文；token、游标、视图及卡片不混版，旧 token 不越权读取新版本 | D/R block |
| G26 | 每个启用的实时来源/交易阶段都有冻结的 `source_policy` 版本与实例配置哈希，报价 freshness/skew 边界夹具通过；未冻结、哈希不一致或夹具未通过时阻断 S2 实时刷新，不自行猜测阈值 | R block |

阈值仅为初始预算，不是交易所规则，也不是已测基线。阈值不满足不能在发布当天临时调低；修改门禁版本、说明原因并重验。`HISTORY_INSUFFICIENT` 等合法个体情况不导致每天全市场无法发布，但其影响率必须可见，异常扩大要告警。已知事实未决率同时按“未决事实/待核验事实候选”与“受影响证券/应判定证券”两套分母统计，避免只看一类分母掩盖缺失。

### 16.3 门禁责任与签署

| 责任角色 | 必须负责的内容 | 发布前签署/证据 |
| --- | --- | --- |
| 产品经理 | PRD 产品语义、状态与规则展示验收标准；确认没有把计算预警误写成公告监管事实 | PRD 验收条款版本；产品语义门禁评审签字 |
| 架构师 | 模块边界、数据/缓存一致性、Redis 并发与故障策略、API/版本隔离、可恢复性门禁定义 | 架构文档版本；技术门禁清单和架构评审签字 |
| 开发负责人 | 门禁可执行实现、迁移/配置哈希、失败修复、构建与运行证据；不得单独豁免阻断项 | 代码提交、构建产物、门禁运行号与缺陷关闭记录 |
| 测试负责人 | 从开发设计派生用例，执行自动化/集成/并发/端到端/真机测试，核对通过证据与报告 | 测试报告、环境/版本/fixture 引用及测试签字 |
| 发布负责人 | 汇总适用门禁和四角色签字；阻断项有任何失败、缺证或超时则停止发布 | 发布清单、批准记录和回滚准备 |

产品经理负责产品预期，架构师负责工程门禁的正确性和覆盖；开发提供可复现证据，测试独立判定通过与否。产品/架构变更须由对应责任人确认后同步 PRD、架构和测试用例。任何 P0 或标记 block 的门禁不得由单人豁免；需要例外时须由产品经理和架构师共同书面定界、列明风险及到期日，并经发布负责人批准。法规/交易规则正确性相关门禁不得豁免。无签字、无证据、报告与输入哈希不符均视为未通过。

## 17. 前端开发与兼容

`datasetStore` 保存最新已接受 generation、读取令牌、日期上下文与实时视图；`refreshCoordinator` 管 S1/S2、名单范围、去重、节流和离页取消；`request_sequence` 阻止慢旧响应覆盖新选股或新版数据。generation 相同而 view_id 不同也不能直接混用一半卡片。

服务端 `tone` 是语义，前端唯一 token 表映射颜色。正文对比度至少 4.5:1；小字号标签同样验证，黄底不直接配低对比黄字。按钮、分段控件、图标和 PDF 链接保留可达顺序、焦点样式与可访问名称；Tab 键在适用键盘环境验证，真机同时验证读屏顺序、触控目标和安全区，不能仅因 WXML 含 aria 就宣布通过。

首屏仅传页面所需字段；列表20条优先，后台名单刷新不因此改成全市场。`setData` 分批且避免每输入一次重绘整页；模拟只更新结果小块。数值与百分号用独立不换行容器，真机覆盖长数字、负号、次数和极端字号，不以省略号隐藏偏离值。

保留历史最多8只、左右滑动、透明背景、与时间栏一致字号、清空图标及扫动、刷新转圈、各卡20rpx间距等已确认交互；不借重构改变成新设计。规则页与隐私页日期/卡片间距、正文字体颜色依现有确认基线回归。隐私文案若未变不自动更新日期假装发生协议变更。

规则页面从读取上下文同版规则取数据；离线缓存注明版本与更新时间，不能声称最新。模拟以支持的算法版本和冻结夹具验收；埋点不含股票搜索词、所选股票清单、openid、模拟值。业务审计可保存证券计算输入，它不是用户查询日志。

旧版兼容至少覆盖实际仍被使用的客户端版本，不按 M7 日期直接把旧 `scope` 返回400。先统计不带用户标识的 API 版本访问量、确认回滚窗口及兼容退出条件，再移除适配器。后端只提供相同真值的旧格式转换，不能为旧客户端保留一套旧规则计算。

## 18. 性能、容量与运行保障

### 18.1 目标及测量条件

以下是拟定验收预算，非本轮实测结果。P1 建立相同实例规格、数据量、请求混合和供应商配额下的基线；未测之前不采购只读副本或保证毫秒数字。

| 项 | 初始目标 | 测量边界 |
| --- | --- | --- |
| 搜索 S1 | P95≤300ms | 后端接收请求至响应，不包含小程序网络 |
| 单股正式读取 S1 | P95≤800ms | 固定版本 DB 读和展示组装，不包含上游实时 |
| 监控/预测列表 S1 | P95≤500ms | 20条首屏，含版本和日期校验 |
| 当日名单 S2 | 同步预算≤1.5s，超过返回202 | 仅当前预测名单，队列完成时间单独统计，不把202算作数据已刷新 |
| 日终主链 | 完整输入到位后≤5min预算 | 不把等待供应商发布时长当计算耗时 |
| 日终端到端 | 正常批次≤15min预算 | 报告外部等待、下载、OCR、计算、门禁各阶段分位；真实晚公告另算重建批次 |
| 发布指针事务 | P95≤100ms预算 | 无外部网络/全量校验 |
| 真机首屏 | 缓存≤800ms；无缓存P95≤3.5s预算 | 明确机型、网络；历史缓存须有日期提示 |

开发阶段压测分阶10/50/100并发；首发预算另按下列双实例负载验收。公开源用 mock 限流不向供应商压测；测试 MySQL连接等待、吞吐、HTTP错误率和CPU，不拿响应延迟当CPU占用推算实例。先给数据按证券建索引、按日批采、消除N+1、为OCR配有界进程池，再按实际瓶颈考虑向量化/多进程；不得为了全市场小于1秒牺牲Decimal边界确定性。

首发容量预算固定为两个云托管实例、100个并发活跃会话持续30分钟（2分钟升压），随后200会话突发5分钟并观察恢复。请求混合按预测首开/刷新40%、单股详情首开/刷新40%、重点监控15%、搜索/规则读取5%；预测/单股按适用时段覆盖 S1、首屏查询和 S2，另将09:00集中进入单列突发场景；单股一半请求集中热门证券、一半分散。200会话突发期间固定并行20个 PDF 下载作为首期混合负载假设；它不是实测用户占比或系统上限。PDF 下载与接口读流量分别测量，不能用比例混合稀释 PDF 或 API SLO。上游实时行情使用 mock/回放限流，不对公开源压测，另以低流量真实源烟测验证链路。

S1 预测/监控列表 P95≤500ms、P99≤1s；单股 S1 P95≤800ms；搜索 P95≤300ms。S2 同步等待≤1.5s，超限即 202，正常 mock 源条件下队列完成 P95≤10s/P99≤20s；202不计刷新成功。非预期 5xx、超时和协议错误率≤0.5%；合法 queued/stale/unavailable 分别统计，不并作成功或错误。错股票、跨池混用、旧 owner 覆盖新视图、stale 参与当前触发/安全判定等正确性违规必须为0。队列完成时间、过期/失败率、资源水位和 PDF 受控下载时延单列报告；不把 PDF 临时缓存写入对象存储，也不以未执行的容量预算宣称通过。

### 18.2 容量预算

按实测行字节和索引估算：日线新增约 `证券数 × 交易日数 × (1+修订率)`；结果新增约 `证券数 × 4指标 × 2方向 × 2角色 × 发布批次数`。例如5500证券，每完整数据集约88000方向结果，30天每天2版约528万行，另有引用/状态/索引。不能仅算一份当前向量后宣称三年仅1.82GB，也不能忽略每次补公告新增版本。

建议热回滚窗口不少于30自然日，同时保护仍有效的客户端令牌；每日发布次数、行大小、压缩率和索引倍率在测试库取样后决定存储预算。原始行情/事实修订按保留数据集引用管理；大结果按现有数据库备份与数据集引用管理，不引入本轮未实现的对象存储归档。PDF 按 §15 的临时缓存策略清理。

MySQL连接池总上限=`API实例数×每实例池+worker池+迁移/运维预留`，小于实际max_connections并留故障余量。新版本 Redis 用于短期共享视图/报价缓存/刷新协调；其连接、容量、淘汰策略纳入发布门禁，掉线不会改变事实或造安全结果。当前冻结版不因本设计修改。

### 18.3 可观测、密钥和备份

结构化日志含 trace_id、job_id、dataset、rule、stage、来源、耗时、错误码；禁止完整URL查询串、token、密码、搜索词和模拟值。高基数股票ID不作为常驻监控label，个股诊断放审计查询。告警包括构建漏跑、指针未更新、覆盖不足、未匹配事实/待核验事实、未知原因异常上升、Tushare失败率、PDF失败、租约失效和连接等待。告警要有送达回执和恢复记录，不是只print。

API/worker账号按需DML，migrate独立DDL权限；生产禁用本地key文件兜底，配置缺失 fail-fast。外部公告下载做白名单、重定向/内网地址防护、文件大小和解析页数限制；CPU和请求预算有界。内部写接口鉴权、幂等、审计，小程序没有发布权限。

核验腾讯云实际备份和时间点恢复能力，记录而不假设“云已备份”。最低演练恢复至隔离库并验证事实、输入清单和结果一致。首发恢复目标为恢复点年龄 `RPO≤24h`、服务恢复耗时 `RTO≤2h`，两者均为硬性门槛而非允许业务事实损失的额度；恢复后必须补采行情/公告并重放事实、重置和数据集，服务恢复不等于全市场数据追平。在公告覆盖、事实账本和结果对账完成前，受影响结果保持 unavailable/待核验，不得以旧数据集显示当前安全；无法重建的已确认事实须保留恢复缺陷并维持不可用。程序回滚、数据回滚和灾备恢复分别演练。

## 19. 测试设计与证据

### 19.1 夹具结构

`fixtures/vnext/{case_id}/` 包括 meta（规则/产品来源、目标日、时钟、预期）、原始输入和修订、公告证据片段/哈希、适用政策及expected。真实案例必须具有可重放原始证据；专家报告或用户期望的“应为3次”只是待核验断言，不能据此倒造expected。合成边界另标 synthetic，不冒充真实交易日。

模拟夹具与后端共用输入，预期由独立公式/人工证据审核生成，不仅比较同一实现复制两次。数值显示误差预算与是否触发分开：正式触发结果必须精确相等；格式化可不同但不能跨阈值。

### 19.2 必测矩阵

| 组 | 样例与主要断言 |
| --- | --- |
| T01 规则边界 | 每板块/特征、上涨下跌、恰等于/上下一个tick、除权构造；4项独立，不被严重项抑制 |
| T02 普通事实 | 单公告单事实、单公告多事实、多公告同事实、多源同事实、区间重叠的不同事实、方向由计算补全、身份未知不能正式计次 |
| T03 同向窗口 | 终点入窗/滑出、开始日在窗外、涨跌分开、N先滑窗再+1、候选不入账、迟到公告追溯及去重 |
| T04 重置 | 普通仅3日；严重四项；风险提示不重置；同日合并；有效日/首收益日；普通停复牌保次数、严重停复牌清次数；临停不自动当复牌 |
| T05 窗口与重置 | 真历史缺口与公告确认后的新基线分开；阈值触及但无公告时绝不写 reset boundary；普通/严重公告按各自指标范围重置；个股缺30日不清空3日 |
| T06 状态组合 | 正常/停牌/终止/退市整理；当前触发与监管中；3日计算触发公告未确认；公告覆盖未完成但行情齐全仍能得出当前安全/风险；已知严重+部分缺数；当日标签次日09:00消失 |
| T07 日期 | 08:59:59/09:00、竞价/午休/连续交易/15:00、收盘集未到、周末/节假日/临时休市、跨年日历缺失、自然/交易日期限 |
| T08 次日反解 | 负r_star、涨跌tick边界、合法区间无交、无价格界限、N窗口不是D再加1日、指数假设和重置生效 |
| T09 预测范围 | 只严重三项及监管内普通3日；单股上下方向完整；名单内当前刷新、不扫描名单外、盘中不重算next、分页面不漏已有名单 |
| T10 故障隔离 | 源失败/未找到/正文坏/不匹配/指数错日/规则不支持；区分行情依赖与公告事实依赖；公告源失败不阻断行情可确定结果；不可用有因、无失败造0、安全；已知严重不被覆盖 |
| T11 并发发布 | 10发布竞争仅一个CAS成功；旧worker失租拒写；封存后不可改；补采修订不混入计算；outbox丢失重试；回滚generation加一 |
| T12 跨页/旧版 | 同令牌三页字段同值；新发布旧响应乱序；新选股旧请求；分页版本换代；旧客户端与v2共享同真值 |
| T13 实时与性能 | Tushare日频静态数据回归；盘中沿用当前实时适配器及回退顺序；预测首次进入S1股票池+S2实时两逻辑阶段各一次且会话去重；非交易/午休跳过S2；名单外不扩池；失败不伪造实时 |
| T16 Redis 实时协作 | 云托管私网 PING/TTL；正常有效租约内同 key/来源/批次最多一轮上游请求；一次租约接管最多重发一次、该来源/报价桶总计不超过两轮，超限返回 queued/unavailable 且不得第三次回源；旧持有者结果拒写；租约续期/compare-token释放；视图部分写入不可见；报价/指数 skew 超差使对应盘中指标不可用；Redis 故障时后端无本地副本回退/无逐实例直连，持久排队或 unavailable；客户端旧画面 stale 仅展示；`context_expired` 重取 S1；MySQL 任务租约与 Redis 短租约分工可验证 |
| T17 PDF | 当前有效条目能下载并在小程序内打开；无效 key、非 PDF、到期事实或无可信来源不生成入口；可信来源缓存未命中时仍返回受控 key并懒重取；清理临时文件且不影响事实 |
| T15 前端/隐私 | iOS/Android长数字和%、20rpx、输入居中、图标动效、历史8只、Tab/读屏顺序、4.5:1、输入与搜索不上传日志 |
| T18 运维迁移 | 空库/旧库升级、迁移中断恢复、Schema兼容、备份恢复、API/worker隔离、生产配置无开发地址、密钥不入包 |

### 19.3 三类对账不能混同

1. **确定性重放**：同清单+同规则+同生成器，业务输出规范化哈希相等；生成时刻/trace_id不参与业务哈希。
2. **新旧实现迁移对账**：最近至少20个交易日和全市场，差异按口径修正、源补齐、事实修订、精度、缺陷等分类；所有风险降级需证据和审核，不要求错误旧值永远不变。
3. **线上一致性**：同读取上下文三页一致，当前/next目标日正确，当前预测名单内实时数据与单股相同。对比时上下文不同只能判需重取，不能直接报算法不一致。

真实回归候选包括凡拓数创、新华传媒、善水科技、冰山冷热、海洋王、百洋股份、近岸蛋白、萃华退、元道退、龙板传媒；每例先核实准确证券身份、目标日、日历及公告，禁止沿用历史对话中的错名/错日期/旧断言。没有原始证据的案例不得写“现网已验收通过”。

### 19.4 验收产物

每次验收包保存commit、schema、rule/hash、input manifest、dataset及generation、门禁运行号、测试报告、差异审批、真机截图/录屏、压力条件和测量、回滚/恢复记录。机检证明规则和一致性；人工审核证明证据解释和视觉体验，二者都需要，不能以“不接受人工确认”为由删掉真正需要人的审核。

## 20. 迁移、发布与下一步安排

### 20.1 现有资产迁移表

| 当前资产 | 处理 |
| --- | --- |
| `server/rules/catalog.py` | 保留类型和已验证规则含义，配置移到唯一规则包；删重复阈值副本 |
| `server/calculations/engine.py` | 保留已验证纯函数结构，统一与service重复的收益/窗口算法；改逐事实终点入窗 |
| `server/resets/decisions.py` | 保留证据追溯原则，升级为修订引用；不把URL/哈希齐全误当事实必定真 |
| `server/data/fact_ledger.py`、监控SQLite | 先一致性导出、hash与来源备份，再迁MySQL事实/修订；未知来源先待核验，不批量人工一键确认 |
| `market_service.py` | 按数据接入、计算、编排、查询逐步抽出；API阶段不再现算正式结果；保留兼容门面过渡 |
| `monitor_service.py` | 公告采集解析与监控查询分离，互相回调改持久化重建事件 |
| `stock_calc_base` 向量 | 只作迁移对照；原始输入与修订可重放后再退役，不把30点向量当完整来源历史 |
| prediction旧表/compat read | v2从统一结果筛选，旧格式由adapter组装；只在访问量和回滚窗口允许后退出 |
| 小程序页面派生函数 | 正式状态、倒计时和阈值迁服务端；保留纯格式化与模拟，单股下拉维持关闭 |
| Tushare静态与盘中实时源 | Tushare仅负责盘后静态数据；盘中沿用当前实时行情适配器及回退顺序，迁移验证实际代码/配置与逐标的来源时间，不新增全市场实时扫描 |

### 20.2 分阶段交付

实施工作量、分工、依赖和观察期见[综合计划 §6](./综合评审结论与实施计划_20261028.md#6-实施计划与分工)。每阶段必须先有可演示产物和通过证据，再进入生产切换：

P0 公共契约/口径与来源定版 → P1 数据和工程地基 → P2 唯一计算/匹配 → P3 公告及原子发布 → P4 前端联调 → P5 全量回放与故障/性能 → P6 灰度 → P7 旧链退役。P1/P2/P3允许基于冻结接口并行，P4先用mock并行，不等待全后端完工才开始。

新增表采用 expand → 回填/对账 → 切读 → 观察 → contract。MySQL DDL存在隐式提交，不能把“放BEGIN里”当可事务回滚保证；迁移文件有校验和、执行阶段、失败恢复和前向补偿，生产不强行执行有数据损失的down。迁移执行前先检查实际MySQL版本与平台能力。

代码发布采用已验证的不可变commit/镜像digest；不在本轮擅自改远端分支或云构建源。先建CI，再评估现有自动部署如何接门禁；不因纯前端变更默认推动后端新规则。后端镜像、规则数据和小程序包分开记录但有兼容发布清单。

灰度按完整数据集/读取渠道隔离，禁止同一数据集混两套规则。至少5交易日影子、3交易日内部体验、5交易日生产观察；异常修复后对受影响门禁重新累计观察证据，不能仅“等时间到了”。回滚保留热数据不少于30自然日；旧链路删除还需满足至少14自然日观察和旧API访问退出条件，选择较晚者。

### 20.3 当前交付边界

本轮已创建显式迁移、规则重放、S1 context/read-token、Redis 协作和受控 PDF 的第一阶段代码与本地合同测试；这些代码尚未获得云端/真机门禁通过。没有运行生产重算，没有提交 git、推送、云端部署或微信上传。工程预算和门禁阈值不冒充测试结果。Q1 次日预测算法与 Q2 公告计次/重置产品口径均已由用户确认；收益构造取证由研发完成，不转嫁为用户选公式。预测同规则去重是已确认展示要求，不再作为待定事项。

## 21. 追溯、引用与文档维护

### 21.1 需求到实现/验收映射

| 产品要求 | 开发设计 | 核心验收 |
| --- | --- | --- |
| 四组简单状态，无额外业务状态 | §3、§8、§16 | T06/T10、G11/G16 |
| 10日同向公告计次、计算核验、逐事实终点入窗 | §5、§7.3 | T02/T03、G07/G08 |
| 普通/严重及两类停复牌重置 | §6 | T04/T05、G07/G12 |
| 四项独立、上下方向和模拟 | §7、§8 | T01/T08/T15、G09/G10 |
| 首开当日实时且只刷新预测名单 | §10、§13 | T09/T13、G19 |
| 当日已触发规则不在次日列表重复出卡、其他规则独立保留 | §7.4、§8.1 | T09、G09；列表去重不改变四项结果 |
| 盘后Tushare、盘中当前行情适配器 | §4.2、§18、§20 | 逐标的 source/as_of 回归T13 |
| 三页同数据版本/时间上下文 | §9、§12、§14 | T07/T11/T12、G09/G13 |
| 公告与事实可追溯、PDF查看和清理 | §5、§11、§15 | T02/T17、G05–G08/G15 |
| PDF 受控下载及临时缓存 | §15、§18 | PDF-01..05、PERF-01、G15 |
| 最大限度减少不可用但不伪造安全 | §11、§13、§16 | T10、G03/G04/G11/G17 |
| 发布稳定、兼容、可恢复、成本可控 | §14、§17–§20 | T11/T17/T18、G14/G20 |

### 21.2 资料来源

- 七份专家报告均为原始评审材料，见[综合报告资料索引](./综合评审结论与实施计划_20261028.md#2-资料与证据边界)。报告互相冲突时，不采用“文件较新就自动覆盖”机制，须经本设计的决策清单收敛。
- Tushare官方日线文档：<https://tushare.pro/document/2?doc_id=27>。用于核对盘后静态日频字段、发布时间和分页限制。
- Tushare官方文档入口：<https://tushare.pro/document/2>。本设计不把 Tushare 声称为盘中实时源；盘中行情适配器与回退顺序以当前仓库实现及部署配置为准。
- MySQL官方Atomic DDL说明：<https://dev.mysql.com/doc/refman/8.0/en/atomic-ddl.html>。Atomic DDL不等于事务性DDL；实施前按实际数据库版本核对。
- 本版本按已批准的 `product_policy` manifest 执行；外部公告或规则链接仅作为补充证据，不是 G21 的必需前提，不把产品口径标为 `official_explicit`。

### 21.3 文档版本维护

用户已确认 Q1 次日预测沿用现有算法；并确认 Q2 普通异动公告直接计次并重置 3 日、严重公告按审定生效日重置四项、行情计算用于补方向/日期审定/核验且不能阻断已确认事实；同时确认当前/次日计算安全不等待公告采集，监管状态由公告事实决定，计算触及只作为可能触发预警。严重公告的日期须结合正文和实际触发数据，D 日收盘触发且当晚公告同一事实时 D+1 生效。规则取证按 PRD §12 的核心边界、证据表和少量固定夹具执行，不要求全市场逐证券逐日覆盖。当前文档待用户最终评审；实现开始后，每个工作包同步开发设计、必要 schema、夹具和验收记录；专家原始报告保持不改，以便追溯。
