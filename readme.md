# 美股量化工作流说明

这份文档只写流程，不写功能清单。

默认前提：

- `Lean CLI` 需要你有可登录的 QuantConnect 账号，并且官方文档当前写明需要处于付费组织下才能使用完整 CLI 工作流。
- `Lean CLI` 本地运行依赖 Docker。
- `TradingAgents` 建议使用 `Python 3.10+`，它当前仓库示例使用的是 `Python 3.13` 环境。

你的当前目录里有两个不同性质的项目：

- [Lean](/data/data_jyz/trade/Lean)：交易引擎源码仓库，适合看源码、改引擎、做底层调试。
- [TradingAgents](/data/data_jyz/trade/TradingAgents)：多 Agent 投研/交易分析框架，适合做个股分析、新闻情绪整合、生成交易判断。

对日常做美股策略这件事，建议这样分工：

- `Lean` 负责：研究后的策略实现、回测、参数调整、模拟盘、实盘执行。
- `TradingAgents` 负责：对 `SPY / QQQ / NVDA / AAPL / MSFT` 这类标的做新闻、基本面、情绪、技术面的多 Agent 分析。

如果你只记一条主线，记这个：

1. 先用 `TradingAgents` 产出研究结论。
2. 把研究结论压缩成明确规则或参数。
3. 再用 `Lean` 把规则写成策略并回测。
4. 回测通过后再上 paper/live。

## Lean

先说最重要的一点：

- 你现在 clone 下来的 [Lean](/data/data_jyz/trade/Lean) 是引擎源码，不是最适合直接写策略的地方。
- 日常开发建议用 `Lean CLI` 新建一个单独工作区；只有当你需要改引擎、查底层行为、打源码断点时，才进入这个源码仓库。

### 流程 1：第一次把 Lean 跑起来

目标：先确认本机能正常使用 Lean CLI 做本地开发。

1. 安装 Python 和 Docker，并确保 Docker 已经启动。
2. 安装 Lean CLI。

```bash
pip install lean
```

3. 登录 QuantConnect 账号。

```bash
lean login
```

4. 在一个空目录里初始化 Lean 工作区。不要在 [Lean](/data/data_jyz/trade/Lean) 源码仓库里做这件事。

```bash
mkdir -p /data/data_jyz/trade/lean-workspace
cd /data/data_jyz/trade/lean-workspace
lean init
```

5. 确认工作区已经生成 `lean.json` 和 `data/`。
6. 到这里为止，不写任何策略，先确认 CLI 环境完整可用。

什么时候算完成：

- `lean init` 正常结束。
- 工作区里能看到 `lean.json` 和 `data/`。

### 流程 2：新建一个美股策略项目并完成第一次本地回测

目标：从零创建一个美股项目，跑通“建项目 -> 写代码 -> 本地回测 -> 看结果”。

1. 进入你的 Lean CLI 工作区。

```bash
cd /data/data_jyz/trade/lean-workspace
```

2. 新建一个 Python 项目，比如 `USStocks`。

```bash
lean project-create --language python "USStocks"
```

3. 进入项目目录，打开 `main.py`。
4. 先不要写复杂逻辑，第一版只做一件事：
   用最小可运行策略验证 `SPY` 或 `QQQ` 数据、时间轴、交易逻辑都正常。
5. 保存代码后，回到工作区根目录，运行本地回测。

```bash
lean backtest "USStocks"
```

6. 等回测结束后，去看输出目录里的结果。
   默认结果会落在项目目录下的 `backtests/<timestamp>/`。
7. 先看三件事：
   - 是否真的拿到了你要的美股数据
   - 是否真的发生了下单和成交
   - 收益、回撤、换手、手续费是否合理
8. 如果这一步都跑不通，不要继续做研究和实盘，先把回测环境和数据路径问题解决掉。

什么时候进入下一步：

- 你已经能稳定回测 `SPY / QQQ / NVDA` 这种标的。
- 回测结果里能看到完整统计和交易记录。

### 流程 3：先研究，再写策略，再回测

目标：把“研究”和“策略实现”分开，避免一边猜一边写。

1. 在工作区中为同一个项目启动研究环境。

```bash
lean research "USStocks"
```

2. 在 notebook 里先回答一个明确问题，不要一上来做大而空的策略。
   例如：
   - `NVDA` 财报后 5 个交易日是否有延续性
   - `QQQ` 跌破某均线后是否有均值回复
   - 标普 500 中高动量股票未来 20 天是否继续跑赢
3. 在 notebook 中验证这个假设，得到一个尽量简单的结论：
   - 条件是什么
   - 入场规则是什么
   - 出场规则是什么
   - 持仓数量怎么定
4. 研究得到结论后，关闭 notebook，把规则写回项目里的 `main.py`。
5. 再次运行本地回测。

```bash
lean backtest "USStocks"
```

6. 回测后不要立刻改参数，先看策略行为是否与研究结论一致。
7. 如果一致，再进入参数调整；如果不一致，优先检查实现是否偏离研究假设。

这条流程的重点：

- notebook 负责回答“这个想法值不值得做”。
- `main.py` 负责回答“这个想法能不能稳定执行”。

### 流程 4：参数调整和结果固化

目标：当基础策略已成立后，再做参数搜索，而不是用参数搜索替代研究。

1. 先固定策略结构，不要一边改逻辑一边改参数。
2. 把要调整的内容限制在少量参数上，例如：
   - 均线周期
   - 止损幅度
   - 持有天数
   - 再平衡频率
3. 先跑一轮基准回测，记录基线结果。
4. 再运行优化。

```bash
lean optimize "USStocks"
```

5. 看优化结果时，先看稳健性，再看收益。
6. 找到候选参数后，再回到普通回测重新验证一次。
7. 只有当“普通回测复现优化结果”时，才把那组参数视为有效候选。

什么时候结束：

- 你手上已经有一组不是拍脑袋的参数。
- 它在重新回测时仍然成立。

### 流程 5：从回测切到 paper/live

目标：在不改变策略主体的前提下，从历史模拟切到真实行情执行。

1. 先确认策略已经通过本地回测。
2. 先上 paper，不要直接上真金白银实盘。
3. 在项目目录中启动本地 live 部署向导。

```bash
lean live deploy "USStocks"
```

4. 在向导中选择券商、行情源、账户和运行参数。
5. 部署启动后，先只观察，不急着频繁改代码。
6. 观察几件事：
   - 行情是否持续进入
   - 订单是否被正常提交和回报
   - 时区、交易日历、市场开闭时间是否符合预期
   - 你的策略在 live 中的行为是否和回测一致
7. 如果需要停止但不平仓，执行：

```bash
lean live stop "USStocks"
```

8. 如果需要平掉仓位并结束部署，执行：

```bash
lean live liquidate "USStocks"
```

9. 只有当 paper 阶段的行为、风控、订单执行都稳定后，再考虑真实实盘。

### 流程 6：你已经 clone 了 Lean 源码，现在什么时候该进源码仓库

目标：区分“策略问题”和“引擎问题”。

1. 先用 Lean CLI 工作流把策略跑起来。
2. 只有出现下面这种情况时，才进入 [Lean](/data/data_jyz/trade/Lean) 源码仓库：
   - 你怀疑某个成交模型、费用模型、数据处理逻辑有问题
   - 你要看某个资产类别在底层是怎么实现的
   - 你要加自定义模块，或者改引擎行为
3. 进入源码仓库后，先编译引擎。

```bash
cd /data/data_jyz/trade/Lean
dotnet build QuantConnect.Lean.sln
```

4. 再根据 [Launcher/config.json](/data/data_jyz/trade/Lean/Launcher/config.json) 指定算法入口。
5. 最后运行 Launcher 验证底层行为。

```bash
cd /data/data_jyz/trade/Lean/Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll
```

正确的时序是：

1. 先在 CLI 层确认是不是策略写法问题。
2. 再进源码层确认是不是引擎实现问题。

不要倒过来。

## TradingAgents

`TradingAgents` 更适合做“分析流程”，不适合直接当成执行引擎。

对你的场景，正确用法不是“让 Agent 直接替你交易”，而是：

1. 让它先分析 `NVDA / AAPL / SPY / QQQ`。
2. 从它的输出中提炼出可验证的结论。
3. 再把这些结论送进 Lean 做回测验证。

### 流程 1：第一次安装并完成一次单标的分析

目标：先把 `TradingAgents` 跑起来，并成功分析一只美股。

1. 进入项目目录。

```bash
cd /data/data_jyz/trade/TradingAgents
```

2. 创建虚拟环境并激活。

```bash
python -m venv .venv
source .venv/bin/activate
```

3. 安装项目依赖。

```bash
pip install .
```

4. 复制环境变量模板。

```bash
cp .env.example .env
```

5. 选择一个 LLM 提供商，先只配一个 API Key，别一开始全配。
   例如使用 OpenAI：

```bash
export OPENAI_API_KEY=你的_key
```

6. 启动交互式 CLI。

```bash
tradingagents
```

7. 在界面里按顺序做选择：
   - 输入 ticker，例如 `NVDA`
   - 输入分析日期
   - 选择 LLM provider
   - 选择 deep thinker / quick thinker
   - 选择研究深度
   - 选择要启用的 analyst
8. 让分析完整跑完，不要中途频繁改模型和配置。
9. 跑完后，先只看最终结论和各 analyst 报告，不急着做任何交易决定。

什么时候算完成：

- 你已经拿到一份完整的 `NVDA` 分析报告。
- 你知道报告输出到了哪个目录。

### 流程 2：拿到第一次结果后，去哪里看报告和日志

目标：学会找到结果，而不是每次只盯终端滚动输出。

1. 完成一次分析后，到默认结果目录查看产物：
   - `~/.tradingagents/logs/<ticker>/<date>/`
2. 重点先看 `reports/` 目录中的内容。
3. 阅读顺序建议固定下来：
   - 先看 `market / news / sentiment / fundamentals` 各分析报告
   - 再看 research team 的多空辩论
   - 最后看 trader 和 portfolio manager 的最终决策
4. 同时看 `message_tool.log`，确认分析过程中到底调用了哪些数据和工具。
5. 如果某次输出明显不合理，先回日志，不要先怀疑结论文本本身。

### 流程 3：换模型或研究深度，再做第二轮分析

目标：控制变量地比较不同模型和不同研究深度，而不是随意切换。

1. 先保留第一次结果，作为基线。
2. 第二次只改一个变量。
   例如：
   - 第一次改模型，不改 ticker 和日期
   - 或者第一次改研究深度，不改模型
3. 重新运行：

```bash
tradingagents
```

4. 再次输入同一个 ticker 和同一个分析日期。
5. 对比两轮结果时，只回答三个问题：
   - 结论是否一致
   - 分歧来自哪一类 analyst
   - 更深的研究是否真的带来了更可执行的信息
6. 如果第二轮只是写得更长、但没有更明确的可验证判断，就不要把它当成“更好”。

### 流程 4：开启 checkpoint，让长分析可恢复

目标：当分析链条较长、模型较重时，避免中断后从头再跑。

1. 先确认你已经能正常完成至少一次分析。
2. 再开启 checkpoint 模式运行。

```bash
tradingagents analyze --checkpoint
```

3. 如果运行中断，下次仍然使用同样的 ticker/date 重新启动。
4. 观察它是否从上次中断位置继续，而不是从头开始。
5. 如果你想清空所有 checkpoint 后重新跑一次，执行：

```bash
tradingagents analyze --clear-checkpoints
```

这条流程适合：

- 分析链很长
- 模型响应慢
- 你不想因为一次中断重跑整条链

### 流程 5：把 TradingAgents 从“会说很多话”变成“能产出可回测信号”

目标：把自然语言分析压缩成 Lean 可验证的输入。

1. 先选一个具体场景，不要让它分析“整个市场”。
   例如只分析：
   - `NVDA` 财报前后
   - `QQQ` 连跌后的反弹窗口
   - `SPY` 大跌后的新闻和情绪变化
2. 跑完分析后，不要直接接受 “buy / hold / sell” 这种文本结论。
3. 从报告里强制提炼出结构化信息：
   - 标的
   - 时间点
   - 看多或看空方向
   - 置信理由
   - 失效条件
4. 再把这些内容翻译成 Lean 能验证的规则。
   例如：
   - 如果 Agent 认为 `NVDA` 财报后强势延续
   - 那就把它翻成“财报次日开盘买入，持有 5 个交易日”的规则
5. 只有翻译成明确规则后，才允许进入 Lean 回测。
6. 如果你翻译不成规则，说明 Agent 输出目前只是观点，不是策略输入。

### 流程 6：用 Python 脚本批量跑单标的分析

目标：从手工交互切到可重复脚本。

1. 先确认交互式 CLI 已经跑通。
2. 再改用 Python 方式调用。
3. 写一个最小脚本，例如：

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

4. 先只跑一只股票，确认脚本化调用和 CLI 结果大体一致。
5. 再扩展到多个 ticker 的批量分析。
6. 批量跑的时候，固定好日期、模型和研究深度，否则结果不可比。

## 串起来的推荐主流程

如果你现在就要开始做美股量化，这条主流程最实用：

### 主流程 A：研究一只股票，然后回测它

1. 用 `TradingAgents` 分析 `NVDA`。
2. 从结果里提炼成 1 到 2 条明确规则。
3. 用 Lean 新建项目，把规则写进 `main.py`。
4. 先跑本地回测。
5. 如果回测成立，再开 notebook 补研究。
6. 如果研究和回测一致，再做参数优化。
7. 最后先上 paper/live，不直接实盘。

### 主流程 B：研究指数或指数成分股，再做组合策略

1. 用 `TradingAgents` 先分析 `SPY` 或 `QQQ` 的宏观、新闻、情绪背景。
2. 用 Lean notebook 研究你的选股或轮动假设。
3. 把组合规则写进 Lean 策略。
4. 跑组合回测，看收益、回撤、换手和集中度。
5. 通过后再做模拟盘观察。

### 主流程 C：发现引擎行为异常时怎么排查

1. 先在 Lean CLI 项目里复现问题。
2. 先确认是不是策略代码问题。
3. 如果不是，再进入 [Lean](/data/data_jyz/trade/Lean) 源码仓库。
4. 通过 `Launcher` 和源码阅读定位到底是数据、撮合、费用还是订单状态问题。

## 你接下来最该做的事

如果你想尽快进入可执行状态，顺序建议固定成这样：

1. 先建立一个 Lean CLI 工作区。
2. 先用 Lean 跑通 `SPY` 或 `NVDA` 的最小回测。
3. 再把 TradingAgents 跑通一轮 `NVDA` 分析。
4. 最后把 TradingAgents 的输出翻译成 Lean 里的明确规则。

不要反过来。

如果你一开始就试图让 Agent 直接替你做交易判断，最后大概率只会得到很多文本，不会得到可验证策略。

## 参考

- Lean CLI Getting Started: https://www.quantconnect.com/docs/v2/lean-cli/key-concepts/getting-started
- Lean CLI `project-create`: https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-project-create
- Lean CLI `backtest`: https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-backtest
- Lean CLI `research`: https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-research
- Lean CLI `live`: https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-live
- Lean CLI `live deploy`: https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-live-deploy
- TradingAgents README: [TradingAgents/README.md](/data/data_jyz/trade/TradingAgents/README.md)
- Lean Engine README: [Lean/readme.md](/data/data_jyz/trade/Lean/readme.md)
