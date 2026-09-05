# 少量知名美股的长历史小时线数据源

原始问题：有没有其他数据源可以获取更长时间的数据。我只需要这几个比较知名的股票的数据，不需要所有股票都有

问答时间：2026-09-02 03:56:41 EDT（UTC-04:00，America/New_York）

## 1. 已确认事实

结论：有，而且“少量知名股票”不值得优先购买全市场数据包。当前最合适的是 FirstRate Data 或 Alpha Vantage；如果目标 ticker 恰好包含在 Kibot Top 50 中，Kibot 的长历史一次性包可能更便宜。YFinance 仍适合快速试跑，但约两年的小时线边界不适合作为长历史主数据源。

| 数据源 | 可用历史与粒度 | 交付方式 | 对当前场景的判断 |
|---|---|---|---|
| FirstRate Data | 美股历史从 2000 年 1 月起；提供 1m、5m、30m、1h、1d | 可按单一 ticker 一次性购买 CSV；热门股票有免费一年 1m 样本 | **首选文件方案**。几个 ticker 可逐个购买；若要严格点时因果，建议买未复权 1m 后自行聚合 1h |
| Alpha Vantage | `TIME_SERIES_INTRADAY` 支持从 `2000-01` 起的 1/5/15/30/60m；用 `month=YYYY-MM` 分月取数 | Premium API，支持 CSV/JSON | **首选 API 方案**。直接取 60m，自动化方便；必须显式设置复权和盘前盘后参数 |
| Kibot | 股票分钟数据声称可追溯到 1998 年；Top 50 包含 30m 等粒度 | 一次性下载包；Top 50 的 30m 当前标价 80 美元 | **最长且可能最便宜**，但先核对所需 ticker 是否在当前 Top 50 清单；30m 需自行合成 1h |
| EODHD | 美国股票 1m 从 2004 年起；原生 5m/1h 仅从 2020 年 10 月起 | API | **最容易复用现有 EODHD 接入**；要获得长历史，必须下载 1m 再聚合 |
| Twelve Data | 官方说明 1m 通常从 2020-02-10 起 | API | 对“更长历史”帮助有限，不优先 |

来源边界：

- [Alpha Vantage 官方文档](https://www.alphavantage.co/documentation/)说明 Intraday 是 premium 端点，历史可从 2000 年 1 月开始，支持 60 分钟、按月查询，并提供 `adjusted` 与 `extended_hours` 开关。
- [FirstRate Data 股票数据页](https://firstratedata.com/stock-data)说明股票数据从 2000 年起、可购买单 ticker，并提供多种分钟/小时粒度；[AAPL 产品页](https://firstratedata.com/i/stock/AAPL)显示其具体覆盖日期和更新方式；[复权说明](https://firstratedata.com/about/price_adjustment)列出未复权、拆股复权以及拆股加分红复权版本。
- [Kibot 官方购买页](https://www.kibot.com/buy.html)列出 Top 50、Top 200 等包的历史起点、粒度和一次性价格。
- [EODHD 官方 Intraday 文档](https://eodhd.com/financial-apis/intraday-historical-data-api)给出 1m/5m/1h 的历史起点、单次查询窗口以及每次请求的 API call 成本。
- [Twelve Data 官方支持文档](https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices)给出其 intraday 历史范围。

对 TradeEngine 的直接约束：当前 Basic v3 Dataset 需要 `time,eventTime,open,close,high,low,volume`。这些供应商都能提供 OHLCV，但供应商时间戳通常只是本地交易时间；导入时还必须建立 `America/New_York` 时区、交易日历和明确的可用时间语义。

## 2. 确定性计算

以 5 个知名 ticker、覆盖 2000-01 至 2026-09 为例：

- Alpha Vantage 按月分页：每个 ticker 321 个月，合计 `321 × 5 = 1,605` 个数据请求。它省去了分钟线聚合，但实际限速和费用取决于所购 Premium 方案。
- EODHD 从 2004-01-01 至 2026-09-02 共 8,281 个自然日。1m 单次最多 120 天，因此每个 ticker 至少 70 个窗口；5 个 ticker 为 350 次 HTTP 请求。该端点每次消耗 5 API calls，即约 1,750 call credits。
- Kibot Top 50 的 30m 数据可两根合成一根 1h；但美股正常交易时段是 6.5 小时，最后一个桶只能是 30 分钟，不能假设每天固定 6 根整小时 K 线。

我做了一个有界样本核验，没有向仓库持久化外部数据：

- FirstRate 官方免费 AAPL 1m ZIP：2,419,183 bytes，SHA-256 为 `c1015f2b8c3bce28a9e343b437273d6d01b325bd47a37b1c7367b84b9777412e`。
- 文件包含 193,829 根数据 bar，范围为 `2022-09-30 04:00:00` 至 `2023-09-29 19:49:00`；字段为 `timestamp,open,high,low,close,volume`。
- 样本中重复时间戳、时间逆序、非法 OHLC、负成交量和零成交量记录均为 0。按 `09:30 <= t < 16:00` 粗分，常规时段 97,749 根、扩展时段 96,080 根。
- Alpha Vantage 的历史月份 demo 请求返回“demo key 仅用于 demo”，未返回历史 payload；因此这里只确认官方接口契约，不能声称已独立验证其 2000 年数据完整性。

## 3. 解释、反证与不确定性

最重要的反证不是“有没有数据”，而是数据能否用于可信回测：

- **复权会产生点时污染风险。** 当前使用今天已知的拆股/分红把整段过去价格重写，不等于当时可观察的原始市场数据。建议把未复权 OHLCV 作为基准数据，把公司行动作为独立事件保留。FirstRate 的说明显示未复权 intraday 主要在 1m 粒度提供，因此直接购买其 1h 文件虽然方便，却未必满足严格点时研究。
- **小时桶不能简单按整点 floor。** 美股 RTH 从 09:30 开始，应定义为 09:30–10:30、10:30–11:30，直到 15:30–16:00；半日市还要依据正式交易所日历缩短。对聚合后的 OHLCV，`eventTime` 使用 bar 结束时刻，`time` 表达 bar 完成后策略最早可见的时间。
- **“知名股票”仍需给出准确 ticker。** Kibot Top 50 是当前流动性篮子，组成可能变化；买前必须逐一核对。指数本身（如 `^GSPC`）、ETF（如 `SPY`）和成分股不是同一种资产，也不能互相替代。
- **数据商自述不等于完整性审计。** FirstRate 的一年样本质量良好，但不能证明 2000 年以来所有 ticker、所有交易日都同样完整。Alpha Vantage 的历史 payload 此次又无法用 demo key核验。购买前应对目标 ticker 索取或购买最小样本，检查缺口、拆股日、DST、半日市和成交量口径。
- **成本仍有未确定项。** FirstRate 单 ticker 的当前成交价和 Alpha Vantage 所需限速档位没有在公开静态页面中形成可稳定核对的统一数字；应以结账页/销售报价为准。Kibot 的 80 美元只对应当前 Top 50 的 30m 包，不是任意 ticker 的承诺。

综合排序：若要最少开发、自动更新，选 Alpha Vantage；若要几个 ticker 的长期归档与可复现文件，选 FirstRate；若 ticker 全在 Kibot Top 50 且接受 30m 聚合，先看 Kibot；若希望最大程度复用现有 TradeEngine EODHD 代码，则选 EODHD 1m，但下载和聚合工作量更大。

## 4. 单一下一实验或 Proposal

建议只做一个低成本试验：用 FirstRate 官方免费 AAPL 1m 样本，构造一个 **RTH、未混入盘前盘后、按 09:30 对齐的 1h Basic v3 Dataset draft**，并与仓库现有 Yahoo AAPL 同期数据做 OHLC/成交量、缺口、半日市和拆股附近差异审计。

固定输入与判定条件：

- 输入：上述 AAPL 免费样本；时区 `America/New_York`；NYSE 交易日历；不前向填充缺失 bar。
- 聚合：每个桶 `open=first`、`high=max`、`low=min`、`close=last`、`volume=sum`；`eventTime` 使用桶结束时刻，`time` 默认同为桶结束时刻，若供应商存在确定的发布延迟则显式后移。
- 产物：CSV 字段严格为 `time,eventTime,open,close,high,low,volume`，同时保存来源 URL、下载时间、原始 SHA-256 和转换参数。
- 通过条件：schema/conformance 全部通过；无重复或逆序时间；正常完整交易日恰好形成 7 个桶（最后一桶 30 分钟）；所有 OHLC 差异都有复权、session 或供应商口径解释。

该试验只验证接入和聚合口径，不发布 Dataset，也不修改 Engine 语义。试验通过后，再根据目标 ticker 清单在 FirstRate、Alpha Vantage 和 Kibot 之间做最终采购选择。
