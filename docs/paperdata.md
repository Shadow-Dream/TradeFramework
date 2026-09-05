# 指数量化论文的数据发布与来源核验

- 原始问题：

  > 这些学术论文有发布一些K线数据吗？他们的数据集哪来的

- 问题时间：2026-09-03 03:48:22 EDT（UTC-04:00，America/New_York；会话未提供原消息的秒级时间，以开始本轮核验的时间记录）
- 回答时间：2026-09-03 03:49:10 EDT（UTC-04:00，America/New_York）
- 核验范围：上一份报告中的 20 篇论文；优先核对期刊页、作者稿、作者数据页、出版社补充材料和官方数据提供方页面。检索截止 2026-09-03。
- 术语：这里把“K 线”严格定义为可按标的和时间重建的 `Open/High/Low/Close`，通常还含 `Volume`。只有收盘价、月收益、因子收益或技术指标都不算 K 线。

## 1. 已确认事实

**直接答案：这 20 篇论文没有一篇公开发布其底层 OHLCV/K 线。** 能找到公开附件的主要是月度收益、预测变量、因子/策略收益或方差风险溢价等论文派生数据。它们可用于复核论文表格或做低频预测，但不能拿来填充 TradeEngine 的股票小时线 Dataset。

### 公开了什么，而不是什么

| 论文 | 官方或作者公开内容 | 底层来源 | 是否为 K 线 |
|---|---|---|---|
| Moskowitz, Ooi & Pedersen (2012) | AQR 提供原论文期的月度 time-series-momentum 多空因子收益，并持续更新月度因子 | Datastream、Bloomberg、交易所等期货/远期数据；股指期货主要来自 Datastream，期货上市前用 MSCI 指数收益 | 否；是 58 个市场组合后的策略收益，不含单个合约 OHLCV。[论文](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf)；[AQR 原论文数据](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data)；[AQR 更新数据](https://www.aqr.com/Insights/Datasets?page=2) |
| Goyal & Welch (2008) | 作者页提供原始研究数据及更新到 2025 年的月度权益溢价预测变量 | 早期 S&P 数据来自 Robert Shiller，1926 年后指数值主要来自 CRSP；另有 S&P、Value Line、NBER、FRED、Ibbotson 等 | 否；是月度指数收益与宏观/估值预测变量。[论文](https://www.nber.org/papers/w10483)；[作者数据页](https://sites.google.com/view/agoyal145/) |
| Neely et al. (2014) | 出版社补充 ZIP，文件名明确为 returns/economic/technical data/programs | 权益溢价和宏观变量沿用并更新 Goyal–Welch；S&P 500 指数及月成交量来自当时的 Google Finance | 否；是月频收益、预测变量、技术指标与程序，不是其底层日线/分钟线。[论文与补充材料](https://pubsonline.informs.org/doi/suppl/10.1287/mnsc.2013.1838) |
| Goyal, Welch & Zafirov (2024) | 作者页提供截至 2021/2025 的 MATLAB、CSV/ZIP 和 Excel 预测变量文件 | CRSP S&P 500 总收益、Ken French 短债收益及多种宏观/市场源；新版部分字段使用 FRED 和 Bloomberg 指数 | 否；是月度预测面板。[论文](https://academic.oup.com/rfs/article/37/11/3490/7749383)；[作者数据页](https://sites.google.com/view/agoyal145/) |
| Bollerslev, Tauchen & Zhou (2009) | 美联储页面提供月度 `Date`、`IV-RV`、`ImpVar`、`RelVar` | VIX 来自 CBOE；S&P 500 五分钟指数价格来自 Institute for Financial Markets；另用 S&P 和 FRED 数据 | 否；公开的是已经聚合的隐含方差、已实现方差和方差风险溢价，不含五分钟价格。[论文与数据](https://www.federalreserve.gov/econres/feds/expected-stock-returns-and-variance-risk-premia.htm)；[数据表](https://www.federalreserve.gov/econresdata/researchdata/feds200711_1.html) |

AQR 对其整个数据馆的说明也很明确：公开文件是论文所描述的**组合收益**，不是底层资产价格或 AQR 产品实盘数据。因此，AQR 的 TSMOM 文件不能反推出 S&P 500、NASDAQ 或 Dow 的 K 线。[AQR Data Library 说明](https://www.aqr.com/Insights/Datasets/About-the-AQR-Data-Library)

### 其余论文的原始数据从哪里来

| 论文 | 研究实际输入 | 数据来源与公开状态 |
|---|---|---|
| Brock, Lakonishok & LeBaron (1992) | DJIA 1897–1986 日收盘序列 | 来自 Dow Jones 历史序列/Pierce 整理资料；论文未发布数据附件，只需要 close，不是 OHLCV。[期刊页](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x) |
| Sullivan, Timmermann & White (1999) | 上述 DJIA 序列及 1987–1996 延长段；1984–1996 S&P 500 期货 | DJIA 基础数据由 Blake LeBaron 提供；期货来自 Pinnacle Data Corporation，并按论文规则拼连续价格。未公开底层数据。[期刊页](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163) |
| Hsu & Kuan (2005) | DJIA、S&P 500、NASDAQ Composite、Russell 2000 的 1989–2002 日收盘及部分市场成交量 | Commodity Systems Inc.；S&P 500 以 NYSE 总成交量做代理，DJIA 成交量由 30 只成分股汇总。无公开附件。[作者稿](https://homepage.ntu.edu.tw/~ckuan/pdf/snoop01.pdf) |
| Zhu & Zhou (2009) | 1926–2004 月度 S&P 500 收益、股息率、期限利差、派息率，用于估计和模拟 | 作者稿引用 Goyal–Welch 的变量定义，但可核验正文未给出可下载 K 线包，也没有完整供应商清单；不应擅自断言全来自 CRSP。[论文记录](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968216) |
| Hurst, Ooi & Pedersen (2017) | 67 个市场月收益；股指含 S&P 500 等，期货上市前以现金指数减短率模拟 | Datastream、Bloomberg；更早时期还用 MSCI、Ibbotson、Global Financial Data、Yale 等。AQR 公开文章和来源说明，但未找到论文底层价格包。[AQR 论文页](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing) |
| Metghalchi, Chen & Hajilee (2016) | NASDAQ Composite 1972–2015 日收盘和日联邦基金利率，共 11,423 个观测 | Datastream。期刊页显示只有论文 PDF，`Download data` 尚不可用。[期刊页](https://ojs.aut.ac.nz/applied-finance-letters/1/article/view/54) |
| Ren, Ren & Forrest (2023) | 对 NASDAQ 均线研究的短篇重检 | 官方页面只有 PDF；可访问的元数据没有写明供应商，也无数据附件。供应商未知，不能根据前一篇论文代填。[官方页](https://digitalcommons.wcupa.edu/pennsylvania-economic-review/vol30/iss2/3/) |
| Fleming, Kirby & Ostdiek (2001) | 1983–1997 S&P 500、美国国债、黄金期货日收益 | 股指和国债期货来自 Futures Industry Institute，黄金来自 Datastream；按成交量切换近月合约。无底层数据附件。[期刊页](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00327) |
| Moreira & Muir (2017) | 市场、价值、动量、盈利、投资、BAB、外汇 carry 等日/月因子收益 | MKT/SMB/HML/Mom/RMW/CMA 来自 Ken French；ROE/IA、BAB 和外汇 carry 来自相应作者。上游部分因子公开，但论文没有发布股票 K 线。[NBER 论文页](https://www.nber.org/papers/w22208)；[Ken French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) |
| Cederburg et al. (2020) | 9 个公开/作者因子和由个股资料重建的 94 个异常组合 | 因子来自 Ken French、Andrea Frazzini、Lu Zhang；94 个异常组合依赖 CRSP、Compustat、IBES 授权库。公开上游因子不等于论文的个股底层数据，未找到官方 replication package。[作者稿](https://www.lehigh.edu/~xuy219/research/COWY.pdf) |
| Gao et al. (2018) | SPY 及其他 ETF 的逐笔成交/报价，构造半小时收益、分钟已实现波动和 15:30 bid/ask | NYSE TAQ，1993–2013。TAQ 是授权产品，论文没有转发原始行情。[作者稿](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)；[WRDS 的 NYSE TAQ 说明](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/nyse-trade-and-quote-taq/) |
| Baltussen et al. (2021) | 60 多个期货市场逐笔数据，抽取一分钟开盘、收盘、成交量；另需期权数据估计 gamma | Tick Data LLC（1974–2020）、OptionMetrics（1996–2017）及 SqueezeMetrics。正文称部分交易时段/成交量图可索取，不是原始 tick/K 线发布。[作者稿](https://academicweb.nd.edu/~zda/intramom.pdf) |
| Lakonishok & Smidt (1988) | DJIA 1897–1986 日序列 | 历史 Dow Jones 序列，由研究助理采集和编程；无官方下载附件，也没有 OHLCV。[期刊页](https://academic.oup.com/rfs/article-abstract/1/4/403/1566965) |
| Sullivan, Timmermann & White (2001) | 百年 DJIA 日序列及 S&P 500 期货 | DJIA 延续 Blake LeBaron/Brock 数据；S&P 500 期货来自 Pinnacle Data。未公开底层文件。[作者稿](https://researchonline.lse.ac.uk/id/eprint/119142/1/dp304.pdf) |
| Coval & Shumway (2001) | SPX/OEX 指数期权 bid/ask，以及其他期货期权收盘价 | Berkeley Options Database、Bridge/CRB Historical Database，VIX 来自 CBOE；研究机构购买的授权数据，未发布。[作者稿](https://www.tylergshumway.org/Coval-ExpectedOptionReturns-2001.pdf) |

这里的共同模式是：论文只描述**来源和变换方法**，读者要自己向供应商取得底层数据；作者最多公开一个不泄露原始行情版权的派生面板。

## 2. 确定性计算

在本次核验范围和上述严格定义下：

- 公开底层 OHLCV/K 线：`0 / 20 = 0%`。
- 找到官方或作者发布、且与该论文直接对应的派生研究数据：`5 / 20 = 25%`，即 MOP 2012、Goyal–Welch 2008、Neely et al. 2014、Goyal–Welch–Zafirov 2024、BTZ 2009。
- 可不经其他市场数据源、直接构造 TradeEngine 股票日线/小时线 Dataset：`0 / 20 = 0%`。
- Moreira–Muir 和 Cederburg 等所用的**部分上游因子收益**可公开下载，但这些是股票组合收益而非 paper-specific K 线，因此未计入上述 5 篇，也不能反算个股或指数 OHLCV。

公开文件的实际用途可以确定如下：

| 数据 | 可以验证 | 不能验证 |
|---|---|---|
| AQR TSMOM 月收益 | 组合均值、波动、Sharpe、因子回归 | S&P 500 单品种信号、展期、盘中成交 |
| Goyal 系列月度面板 | 权益溢价预测回归和月度择时 | 日内/小时策略、OHLC 路径 |
| Neely 补充数据 | 论文的宏观/技术指标预测实验 | Google Finance 原始行情的逐条完整性 |
| BTZ 月度方差面板 | 方差风险溢价预测回归 | 五分钟 realized variance 的清洗、坏点和交易时段处理 |

尤其要注意“仅有 Close”也不是 K 线。Brock、Hsu–Kuan、Metghalchi 等日频论文的技术规则主要只需收盘价，因此论文能够完成研究，却没有产生或保存可供我们使用的 O/H/L/V。

## 3. 解释、反证与不确定性

- **没有公开 K 线不代表论文无法复现。** 月频策略只需要月收益或因子收益即可复核统计表；但这叫“结果层复现”，不是“市场数据层复现”。若要检查公司行动、交易时段、期货换月或异常报价，仍必须拿到底层数据。
- **许可是主要原因。** CRSP、Compustat、TAQ、Datastream/LSEG、Bloomberg、Tick Data、OptionMetrics、Pinnacle、Berkeley Options Database 等按机构授权提供数据。论文作者通常可以发布计算结果，却无权把原始行情重新打包公开。
- **指数、ETF 和期货不能互换。** 老 Dow 论文多用不可交易的现金指数 close；Gao 用 SPY；趋势论文多用股指期货连续序列。即使都代表“美国大盘”，股息、费率、隔夜时段、基差和换月都会改变回测。
- **派生数据无法唯一反推 K 线。** 同一月收益可以由无数条不同的小时价格路径产生；策略组合收益又叠加了波动率缩放和跨市场权重，因此从 AQR 月收益“还原 S&P 500 行情”在数学上不可识别。
- **公开文件也可能不是 point-in-time。** Goyal 数据会持续修订和延长；新版还包含 Bloomberg 等商业源字段。做历史预测时必须冻结下载版本、记录当时可得日期，并逐字段检查修订与再分发条款。
- **两处来源仍有明确不确定性。** 可访问的 Ren et al. 官方元数据未披露供应商；Zhu–Zhou 作者稿可确认变量、期间和对 Goyal–Welch 定义的引用，但不足以把全部底层来源归为某一家供应商。报告刻意保留“未知”，不以相邻论文或常识填补。
- **“作者公开”不自动等于“可商业再发布”。** AQR、作者主页和政府页面可供研究下载，但进入产品、共享仓库或再分发前仍应保存并审查各自条款；本报告只确认可访问性，不作法律授权判断。

因此，对“能否从论文附件拿到长历史 S&P 500/QQQ/DIA 小时线”的结论很确定：不能。这批附件最适合作为辅助因子或论文复核基准，底层 K 线仍需独立采购或从多个行情源聚合。

## 4. 单一下一实验或 Proposal

下一步只做一个有界实验：**审计并冻结上述 5 份官方派生数据包，判断它们能否作为 TradeEngine 的辅助研究数据，而不尝试把它们冒充 K 线。**

协议如下：

1. 下载到隔离的 staging 目录，逐文件记录原始 URL、`retrieved_at`（UTC）、SHA-256、文件名、大小和可见许可/使用条款；不改 Engine，不发布 Dataset。
2. 盘点字段、频率、首末日期、缺失值、重复键和日期是否代表 observation、period-end 还是 release date；补充材料若含代码，核对代码实际读取的文件与论文声称来源是否一致。
3. 以明确标准分类：能直接重算论文主表的标为 `research_reproduction`；只有最新修订面板但不能重现论文 vintage 的标为 `updated_reference_only`；含商业源且再分发不明确的标为 `restricted`。
4. 成功判据是 5 份数据各自产生一条可审计清单，并能明确回答“可进入辅助特征 Dataset / 仅作外部基准 / 不可发布”三选一；任何一份都不得被用于生成或补齐 SPY、QQQ、DIA 的 OHLCV。

这个实验能先把论文公开数据的研究价值和许可边界钉死。之后若目标仍是长历史小时线，应单独评估 TAQ、Tick Data、Polygon/Alpaca 等行情源及聚合一致性，不能把论文附件当作捷径。
