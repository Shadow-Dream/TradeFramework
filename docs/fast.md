# Projection 性能改造报告

## 问题记录

用户原始问题：

> 行，上述改进全部实施即可。

- 问题时间：2026-08-28 15:45 EDT（America/New_York；会话界面未提供秒级消息时间）
- 回答时间：2026-08-28 18:00:40 EDT（America/New_York）

## 结论

本轮已把可安全落地的性能改进放入 Trade Engine 通用路径，而不是 Basic 子系统的特化计算路径。2500 根日线的真实无头 Chrome 基准中，添加 Bollinger Bands 的三轮中位数如下：

| 场景 | 改造前 | 改造后中位数 | 降幅 |
|---|---:|---:|---:|
| 首次添加 BB | 1077 ms | 620 ms | 42.4% |
| 同一浏览器再次添加 BB | 415 ms | 237 ms | 42.9% |
| 新页面、服务器缓存已热 | 667 ms | 411 ms | 38.4% |
| 首次删除 BB | 179 ms | 90 ms | 49.7% |
| 再次删除 BB | 189 ms | 64 ms | 66.1% |

首次添加已稳定进入一秒以内。三次独立运行的首次 BB 分别为 633、608、620 ms；浏览器热添加分别为 252、237、196 ms。

## 最终路径

```text
不可变 Backtest/Sample Result
        ↓
Engine 内容寻址 Projection Cache + 同键 single-flight
        ↓ miss
按 Result 粘连的常驻 Projection Worker 池
        ↓
已验证 frame LRU + 纯 Graph 编译计划 LRU
        ↓
每次请求新建隔离、受监督的临时 Signal 执行子进程
        ↓
columns-v2 → gzip → 浏览器解码 Worker
        ↓
Chart Core 增量 reconcile，仅增删受影响 series
```

## 已实施项目

### 1. Engine 级持久精确缓存

- 缓存位于 Engine `controlRoot/result-projections`，不再由 Basic 子系统拥有。
- key 覆盖 Engine runtime identity、Result ID 与内容摘要、排序后的路径、完整临时 Module 实例、精确 Module Definition 摘要、投影格式和窗口。
- 命中返回原 Worker 生成的规范 JSON 字节；损坏、摘要不符或身份不符时删除该项，并通过同一权威 Worker 路径重算。
- 同进程线程锁和跨进程文件锁组成同键 single-flight。
- 上限为 64 项、总计 256 MiB、单项 64 MiB，并按最近使用证据淘汰。
- Backtest 和 Sample Result 共用同一通用实现；HTTP 响应返回 `cache.hit/cacheKey/payloadDigest/payloadSize` 证据。

### 2. 常驻但隔离的 Projection Worker 池

- Engine 管理两个 Worker slot，按 Result 内容摘要稳定分配；不同 Result 可并行，同一 Result 保持 frame 热缓存局部性。
- Worker 只缓存验证后的 Result frame 和纯编译计划，不缓存可变 Signal 实例。
- 每次包含临时 Module 的投影仍 fork 新执行子进程，重新构造 `ResultCycleProcessor` 和 Module invoker，并执行既有 descendant 清理证明。
- Worker 死亡后当前请求明确失败，不在同一请求内悄悄切换实现；下一请求重新建立受监督 Worker。
- 收藏预热使用去重优先队列；交互请求可将尚未执行的同一任务提升到队首。完成证据绑定 Worker generation，避免每次磁盘缓存命中都重复预热。

### 3. `columns-v2` 列式投影

- 保留旧 `rows` 作为兼容和测试 oracle；所有图表入口改用版本化 `columns-v2`。
- 列式结果包含精确 schemaVersion、总行数、半开窗口、稳定列顺序、每列 values，以及区分“缺字段”和显式 null 的 absent 索引。
- Engine 用磁盘 spool 流式生成列，内存不随总行数线性膨胀；最多 256 个请求列。
- 窗口只限制输出编码。临时 Signal 仍从第 0 个 cycle 执行，warm-up 与状态语义不会被窗口截断。
- Chart Core 直接读取列，不再把它膨胀成逐行 Data Dict 列表。

### 4. 网络和浏览器主线程

- 64 KiB 以上 JSON 在客户端接受时使用确定性 gzip；正确尊重 `gzip;q=0`。
- 代表性 2500-bar 请求的 HTTP Content-Length：蜡烛投影由 823,732 B 降至 35,429 B，BB 投影由 878,732 B 降至 37,689 B，均减少约 95.7%。
- Projection 响应以 transferable ArrayBuffer 交给同源浏览器 Worker 做 UTF-8/JSON 解码；Chart Core 随后执行精确列合同校验。
- Visualizer 输入 schema 每个端口只规范化一次；不再对每根 bar 递归重复规范化。输入仍经过字段衰减并被冻结，未扩大 Renderer 权限。
- 该项把 2500-bar `prepareFinancialPane` 从 166–310 ms 降到约 18–100 ms；同一浏览器冷/热差异来自 JIT 和对象缓存。

### 5. 保存、投影和图表增量更新

- Basic 指标修改同时启动 CAS 保存与投影；只有保存成功且 revision 未冲突时才安装预投影结果。
- 保存失败不展示草稿；revision 冲突只展示服务器当前版本。
- Chart Core 增加通用 Pane session reconcile。实例、Renderer 和数据源未变的 series 保持原对象；新增/修改/删除只处理受影响 series，并传递更新到依赖它的 overlay。
- Basic 工作区、Trade Engine Result 页面和独立 Chart 页面都接入该通用 reconcile。
- 可视时间范围、蜡烛 series、Pane chart 与绘图交互 controller 在普通指标变更时保留。

### 6. 精确窗口能力

- Engine 和 HTTP 合同支持 `{startIndex, endIndexExclusive}` 视窗输出，并纳入缓存身份。
- 当前 2500-bar 基础工作区仍请求完整历史；没有为了基准数字静默截断历史或做有损抽样。
- 大数据客户端可显式使用窗口，且指标仍从 cycle 0 计算。后续若启用自动分段加载，可复用这一合同而不改变 Signal 结果。

## 一致性与无 Shortcut 说明

- K 线和指标继续来自 `Sampler → Result DataKey → 临时 Signal → Visualizer → Chart Core`。
- 没有直接读取原始 CSV/K 线并在页面硬算或硬画指标。
- 没有针对 BB、SMA 或 Basic 添加第二套计算器。
- 没有跨配置复用部分 Signal 输出；相同精确请求复用完整规范投影，不同配置仍执行 Module。
- `rows` 与 `columns-v2` 的值、null、缺字段和 warm-up 已做 oracle 对照。
- 缓存、gzip、浏览器 Worker 和增量 series 都只改变准备、传输和挂载方式，不改变 Module 顺序或数值语义。

## 验证证据

- 核心/前端/架构定向回归：110 passed。
- Projection Worker、持久缓存、窗口、列式 oracle、gzip、浏览器解码 Worker、Chart Core reconcile 均有独立测试。
- 扩展 Result、Basic 服务与架构回归在修正公共边界后通过；没有扩大依赖白名单。
- 三轮生产路径无头 Chrome 基准全部 `accepted: true`，每轮覆盖冷打开、浏览器热打开、冷添加、删除、浏览器热添加、新页面服务器热打开和服务器热添加。
- 固定数据：2500 根确定性日线；provider 只下载一次。基准文件保存在 `/tmp/fast4.json`、`/tmp/fast5.json`、`/tmp/fast6.json`，不写入仓库。

## 性能边界

首次冷打开仍包含认证、目录加载、Sample Result/缓存校验、Worker 启动和首个 Chart 初始化，三轮中位数为 763 ms；热浏览器打开中位数为 282 ms。首次新指标仍必须真实执行一次对应 Signal Module，因此不会伪装成零计算。后续同一 Result、同一路径、同一配置由精确缓存命中。
