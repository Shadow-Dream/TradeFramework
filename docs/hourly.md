# 标普 500 小时线数据源与 TradeEngine 适配评估

原始问题（原文）：

> 看一下tradeEngine的股票子系统。我想下载一些数据来测试。有没有什么平台能提供比较完整的 标普500 小时线数据？YFinance可以吗

问答时间：2026-09-02 03:47:02 EDT（UTC-04:00，America/New_York）  
代码基线：Git `cae76fbf4578081a47a37f036b7beae46f2fb9c3`

## 已确认事实

先区分两个目标：`^GSPC` 是一条标普 500 指数序列；“标普 500 成分股小时线”是数百只证券的面板数据，还必须定义使用当前成分股，还是每个历史时点当时有效的成分股。后者若只拿今天的名单回填历史，会产生幸存者偏差。

TradeEngine 当前股票入口位于 `application_subsystems/basic.py`，市场 Provider 位于 `application_protocols/basic_workflow/market_data.py`：

- `nasdaq-us-snapshot` 同步 NASDAQ Trader 的美国上市证券目录，但每个标的只声明 `availablePeriods=["day"]`；历史接口最多请求十年、5,000 条日线，而且输出不含 volume（`market_data.py:237-283, 444-478`）。
- `eodhd-demo-us` 只提供 AAPL、AMZN、TSLA、VTI，固定最近 31 天日线；它保留 OHLCV、独立的 event/available time、原始响应 SHA-256、调整策略和 revision policy（`market_data.py:379-426, 481-552`）。
- 在线股票页面的 Dataset 物化是单标的一次一个快照。当前 v3 合同要求 CSV 为 `time,eventTime,open,close,high,low,volume`，并明确拒绝一个 Dataset 中出现多个 instrumentId（`dataset_adapters/basic_workflow_v3_conformance.py:122-145, 162-219`）。因此，把全体成分股下载下来并不会自动变成一个可做横截面选股的 v3 Dataset。
- 离线 `mining` 子系统当前标记为不挂载，唯一真实 Provider 是 Binance Spot，不是股票数据入口（`mining/README.md`）。

仓库已经存在 Yahoo Finance chart API 的离线样例，但它不是当前股票页面的在线 Provider，也没有使用 `yfinance` Python 包。`scripts/prepare_tlm01d02_evaluation_data.py` 直接请求 Yahoo chart endpoint，小时窗口限制为不超过 729 天，并把 provider bar start 移到 `min(start+1h, regularSessionClose)` 后才视为可用（`scripts/prepare_tlm01d02_evaluation_data.py:80-105, 118-190, 321-429`）。现有样例覆盖 2024-08-23 至 2026-08-20：`^GSPC`、`^IXIC`、NVDA 各有 3,469 根 RTH 小时线。需要注意，样例最终是 v2 CSV，只写 OHLC，原始 Yahoo volume 没进入 Dataset（`scripts/prepare_tlm01d02_evaluation_data.py:257-270`）。

YFinance 对“先测试”是可用的。它支持批量 ticker 和 `interval="1h"`；当前 API 文档笼统写着 intraday 只能取最近 60 天，但当前源码对 `1h` 又明确使用 729 天拒绝阈值，仓库中的 729 天 Yahoo 实测也与后者一致。这个文档冲突意味着“两年小时线”应视为当前观察到的行为，不是稳定 SLA。[yfinance download 文档](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)；[yfinance 当前源码](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)（资料检索于 2026-09-02 03:47 EDT）。YFinance 自身声明它未获 Yahoo 背书、面向研究教育用途；Yahoo 当前条款还限制未经许可的自动化采集，因此不宜把它当生产或可再分发的数据合同。[yfinance 法律声明](https://ranaroussi.github.io/yfinance/)；[Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)（资料检索于 2026-09-02 03:47 EDT）。

如果“比较完整”指全体美国交易场所的成分股成交数据，候选平台如下（价格和覆盖均为 2026-09-02 03:47 EDT 检索结果，购买前应再次确认许可）：

| 平台 | 已核实覆盖 | 对本项目的判断 |
|---|---|---|
| Massive（原 Polygon.io） | SIP 全美股票 1 分钟 OHLCV flat files，历史自 2003-09-10；个人计划当时为 $29/月 5 年、$79/月 10 年、$199/月全历史。[官方 minute aggregates](https://massive.com/docs/flat-files/stocks/minute-aggregates) | 数据完整度优先的首选；下载分钟线后按正式交易时段聚合成小时线。仍需另配历史标普成分表。其 reference API 支持按日期查询 active/delisted ticker，可保留退市证券。[官方 point-in-time tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers) |
| EODHD | 美国股票 1 小时线自 2020-10 起，单请求最多 7,200 天；另有覆盖最多 12 年的 S&P/Dow 历史成分 API（当时 $29.99/月）。[官方 intraday 文档](https://eodhd.com/financial-apis/intraday-historical-data-api)；[历史成分 API](https://eodhd.com/marketplace/unicornbay/spglobal) | 与现有 EODHD Provider 结构最接近，也是“行情 + 历史成分”最省集成工作的方案；正式接入仍须新增认证、小时周期和非 demo 审计。 |
| Alpaca | 历史股票数据自 2016；$99/月计划提供全美交易所 SIP，免费层只有 IEX。官方称 IEX 约占 2.5% 市场成交量。[官方计划](https://docs.alpaca.markets/us/docs/about-market-data-api)；[feed 说明](https://docs.alpaca.markets/us/docs/historical-stock-data-1) | API 友好；付费 SIP 可作为中期方案，免费 IEX 只适合接口冒烟，不能称为完整小时成交量。 |

## 确定性计算

对仓库现有 Yahoo 样例做了只读复核：重新运行 Basic Workflow v2 CSV conformance，并分别重算小时原始响应、历史日线原始响应和 ZIP 包 SHA-256；SPX、NASDAQ_COMPOSITE、NVDA 三组的 conformance 与三个 digest 均和 `artifacts/dataset-workspaces/tlm01d02-evaluation-20260821/evidence.json` 一致。

SPX 精确输入与结果：

- 小时原始响应：`sha256:c7519465e3bedc02e9166f2be77c424237766c4d9aa2e1856acfb7884baa81eb`
- 历史日线原始响应：`sha256:4b780089d02267396309a57a4c69cca20e5fff4cc57ea1fe3956c9281f538dce`
- Dataset ZIP：`sha256:3eb3af9252c55bab5c9f9c3564581ad9a0d902938d854ed54c280a2c7e175616`
- 行数：hour 3,469、day 1,080、week 225；三文件合计 4,774 行。

若用同一 729 天窗口粗略扩到 500 只股票，小时行数是 `3,469 × 500 = 1,734,500`。这是容量估算，不代表 500 只股票都会有完全相同的交易历史；IPO、停牌、退市、缺失 bar 和成分变更都会降低或改变实际行数。

## 解释、反证与不确定性

结论是：YFinance 可以用来验证下载、清洗、时区、Dataset 发布和小时策略链路；不适合证明“完整标普 500 历史回测”成立。

主要反证与边界如下：

- `^GSPC` 的小时线只代表指数点位，不是 500 家公司的逐股行情；SPY 又是可交易 ETF 代理，存在费用、分红和跟踪差异。
- 今天的成分股名单不是历史时点名单。行情再完整，如果 membership 不是 point-in-time，回测仍然不完整。
- YFinance 的 1 小时历史深度存在官方文档与实现不一致；Yahoo endpoint、限流和历史修订没有 SLA。仓库样例已经观察到每个标的 14 条不完整 provider row 被丢弃，以及 1 个 session-close snapshot 被合并。
- `yf.download` 当前默认 `auto_adjust=True`。若不显式固定 `auto_adjust`、corporate actions、RTH/pre-post、timezone 和 missing-bar 策略，不同下载批次可能不是同一价格定义。[yfinance 参数文档](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)（检索于 2026-09-02 03:47 EDT）。
- 当前 Yahoo 离线脚本虽然解析了 volume，却在 v2 输出中丢弃 volume；直接复用它不能测试依赖成交量的指标。要保留因果时间与成交量，应生成每标的 v3 Dataset，或先另行设计并验证多标的 OHLCV profile，不能仅凭相同列名假定兼容。
- Massive 最接近“完整成交数据”，但大文件、分钟到小时的 session 聚合、拆股/分红政策、授权和历史 membership 仍是独立问题。EODHD 更省集成，但其数据源方法和历史深度应通过小样本与 SIP 源交叉核验后再用于重要研究。

可证伪标准：若同一 ticker/window 的重复下载不能得到稳定的有序 bar 集合，或与一份 SIP 基准在 RTH bar 数、OHLC、volume、拆股边界上出现无法解释的差异，则否定 YFinance 作为该研究数据源，仅保留它作为 UI/协议冒烟数据。

## 单一下一实验或 Proposal

建议先做一个不购买数据的最小实验：固定 `^GSPC, SPY, AAPL, MSFT, NVDA, AMZN, META, BRK-B, JPM, XOM` 十个 ticker、最近 729 天、RTH、`interval="1h"`、`auto_adjust=False`、`actions=True`；保留每次 Yahoo 原始响应及 SHA-256，然后每个 ticker 分别生成 v3 CSV：`time,eventTime,open,close,high,low,volume`。

唯一验收是：十个 Dataset 全部通过 v3 conformance，并输出每个标的的请求边界、exchange timezone、bar 数、首末时间、缺失/重复/乱序/非有限值计数、corporate-action 边界和 digest；再随机选 AAPL、SPY 与一份 Alpaca SIP 或 Massive 数据逐 bar 对照。若覆盖率和定义满足测试要求，再决定扩大到全体 point-in-time S&P 500；若不满足，优先转 EODHD（集成省事）或 Massive（数据完整度优先）。本报告没有执行该下载或发布 Proposal。
