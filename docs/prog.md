# 基础指标库与 Wickra 接入进度回忆

用户原始问题（原文）：

> 回忆上次进度。上次进行到基础指标库构建和Wickra接入

- 问题时间：2026-08-27 08:15:19 EDT（UTC-04:00，America/New_York；会话未暴露原消息时刻，以本轮核查记录时刻代记）
- 回答时间：2026-08-27 08:15:19 EDT（UTC-04:00，America/New_York）

## 已确认事实

1. 当前代码基线只有一个提交：`911bc5c37d8113024c4a34242d8f07f578ddf188`，提交时间为 2026-08-20 13:15 EDT。核查前工作树有 104 个修改、8 个删除、49 个未跟踪条目，说明后续工作尚未形成可按提交恢复的阶段边界。（来源：`git log`、`git status`；截至 2026-08-27 08:15 EDT）

2. 仓库基线已经包含 11 个自研流式指标 Module：SMA、EMA、WMA、VWMA、RSI、MACD、Bollinger Bands、ATR、Stochastic、OBV、ROC。实现位于 `builtin_implementations/pipeline/*_indicator.py`，公开端口与配置位于 `builtin_implementations/pipeline_contracts.py`；这些文件已被 Git 跟踪，指标实现相对基线没有新 diff。因此它们是现有“基础指标库底座”，不是昨夜新接入的 Wickra 适配层。（来源：Git 基线、`git ls-files`、`git diff`；截至 2026-08-27 08:15 EDT）

3. 与本主题时间上最接近的新产物是未跟踪文档 `docs/tradingview_builtin_technical_indicators.md`，最后修改时间为 2026-08-27 00:58 EDT。它从 TradingView Supercharts 的 `Built-in → Technicals → Indicators` 整理了 127 项当前清单，并明确区分了官网 UI、Advanced Charts 107 项清单和帮助文章目录。说明上次已经完成“候选基础指标全集”的第一份外部口径盘点。（来源：该文档内容及文件时间；截至 2026-08-27 08:15 EDT）

4. Wickra 尚未落入本地工程：仓库内没有 `wickra` 引用，没有依赖声明，没有适配器或专属测试；系统 `python3` 环境中 `find_spec("wickra")` 与包版本查询均为空。当前唯一依赖文件 `requirements-workspace.txt` 只声明了 JupyterLab。（来源：仓库全文检索、Python 包元数据、依赖文件；截至 2026-08-27 08:15 EDT）

5. 聚焦回归 `python3 -m pytest -q tests/application_protocols/test_builtins.py tests/engine/module/test_python_adapter.py` 当前为 `22 passed in 15.84s`。这证明现有 BuiltIn 安装、声明和 Python Module 生命周期没有被当前工作树破坏；它不证明 11 个指标与 TradingView、TA-Lib 或 Wickra 数值一致，因为现有显式指标行为测试只直接覆盖 SMA 的基类接入和 Bollinger Bands 的 `k=0` 情形。（来源：本轮测试与测试用例检索；截至 2026-08-27 08:15 EDT）

6. Wickra 官方资料将其描述为 Rust 核心、Python 绑定、逐 tick 增量更新的指标库，当前文档列出 514 个指标及 Python `update`/`batch` 接口；这与 Trade Engine 的 `initialize -> invoke* -> finalize -> close` 流式模型在调用形态上相容。[Wickra Overview](https://docs.wickra.org/overview)；[官方 GitHub](https://github.com/wickra-lib/wickra)。（外部资料截至 2026-08-27 08:15 EDT）

7. Trade Engine 的公开 Module 合约要求只接收已声明端口、只输出已声明结果，并且 checkpoint 必须是有限 JSON；默认 `on_snapshot`/`on_restore` 只持久化 `self.state`。因此 Wickra 原生对象即使能逐 tick 调用，也不能在未设计可验证恢复语义时直接塞入现有 Module。（来源：`.agents/skills/strategy-development/references/engine-contracts-v1.md`、`strategy_devkit/module_sdk.py`；截至 2026-08-27 08:15 EDT）

## 确定性计算

- 计数方法：枚举 Git 跟踪的 `builtin_implementations/pipeline/*_indicator.py`，得到 11 个指标实现；枚举 TradingView 清单表格，文档记录 127 项。
- 现有指标实现集合摘要：`sha256:990ff2d2686a9a7dbe15668f12619985d8e1f714b4e002b0aa51daf41037c417`。计算方式为按路径排序各 `*_indicator.py` 的 SHA-256 清单后再次 SHA-256。
- TradingView 清单摘要：`sha256:be5837a479bf9e6e462f6ee00ccda51af5f650a214ead7f49c0dc1d3114ecd64`。
- Pipeline Module 合约摘要：`sha256:4c5615d1538b238974ef96e6d145ea95525ba33560dc5018d54aab9efb78e8d5`。
- 依赖声明摘要：`sha256:393efdb3c897388beb48907cbdf99fa6aa65d848735292c91b4d6a9989412627`。
- 报告生成前 Git 状态摘要：`sha256:04886818929a10c9c7a2e97591c699cfe015bf78c213c15c121d7eb31c47f23a`。

## 解释、反证与不确定性

- 最可信的进度定位是：**指标范围盘点已完成第一版，Wickra 选型/接入可行性已经开始，但代码、依赖和验证尚未落盘。** 这比“Wickra 已接入”更符合现有证据。
- 现有 11 个 Module 可以作为端口、配置和生命周期样板，但不能直接当作数值基准。RSI、ATR、EMA 等指标存在初始化、warmup、平滑方式等实现口径差异；当前测试没有建立与第三方参考值的逐点等价证据。
- Wickra 官方资料展示 `reset`、`update` 和批/流一致性，但本轮没有找到可直接满足 Trade Engine 有限 JSON checkpoint 的官方状态导出/恢复接口。反证条件是：在固定 Wickra 版本的公开 API 中找到并验证确定性的可序列化 state round-trip。
- 外部版本信息存在时间差：本机 crates.io 查询返回 `wickra = 1.0.1`，而 Wickra 文档页面仍列 `0.9.9`。正式接入前必须以目标 Python 包仓库和 wheel 为准固定精确版本及哈希，不能使用未固定的“latest”。
- 若仓库外曾有临时草稿、未保存对话代码或其他 workspace，本轮仅凭当前公开工作区无法恢复；出现 Wickra 适配器、锁文件或 parity 测试即可推翻“尚未落盘”的判断。

## 单一下一实验或 Proposal

只做一个最小判别实验：在 Agent workspace draft 中新增一个 **Wickra RSI 适配器原型**，固定精确 Wickra Python 版本与 wheel 哈希，保持 Engine、现有 11 个 BuiltIn Module、Pipeline、Environment、Analysis 和 Dataset 全部不变；用固定 OHLCV/close fixture 验证四件事：逐 tick 输出、warmup 边界、`batch == streaming`、以及 `snapshot -> 新实例 restore -> 后续输出` 与不中断运行逐点一致。

成功标准是输出与固定 Wickra 参考逐点一致且 checkpoint 为有限 JSON；失败则能一次性区分问题来自依赖封装、数值口径还是状态恢复。该实验尚未执行，也不应在通过公开 Engine validation 前发布或替换现有 `rsi-indicator`。
