# Basic Workflow 应用组件库

这里保存按应用协议开发、但不依赖某一条策略身份的可复用组件。组件仍以普通
Module 资源发布；TradeEngine 不包含任何协议或策略特化。

`basic_workflow` 当前分为三层：

- `account.py`：单资产账本、多资产盯市账户和多仓位多空头寸盯市计算；
- `brokerage.py`：手数、可卖空、杠杆、现金购买力、目标差额、滑点、成交、费用、
  结算和负现金利息规则；
- `performance.py`：年化收益、样本波动率、Sharpe 和回撤计算。

这些文件是无状态、确定性的计算内核。`builtin_implementations` 中的普通 Module 适配器
负责端口、配置和 lifecycle state，并复用这里的计算，不复制公式。
`catalog.component_catalog()` 返回可直接归档的账户、券商规则和 Analysis Module 定义；
它只是应用层目录，不是 Engine 注册表或隐式执行路径。

基础协议不固定某个 Analysis 输出。年化、Sharpe 等指标由
`performance-metrics-analyzer` 这一更下一级资源声明；策略也可以替换为其他 Analyzer。
