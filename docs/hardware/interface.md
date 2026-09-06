# 接口与流水控制

这一章描述所有 Chisel 算术模块共享的 `UnitIO`。模块由 `ArithmeticModule` 派生，顶层接口定义在 `Interface.scala`；算术数据通路不直接规定握手时序，而由 `ElasticModule` 或 `IterativeModule` 管理。

## Request 与 Response

`Request(w)` 的字段为 `a`、`b`、`c`、`rounding` 和 `tag`。`a/b/c` 的宽度均为模块格式宽度 `w`；二元操作忽略 `c`。`rounding` 为三位编码，合法范围为 0 到 4；模块在 `io.in.fire` 时断言该范围。`tag` 固定为 32 位，计算和传输模块均原样返回它。

`Response(w)` 的字段为 `bits`、`flags`、`tag` 和 `remainder`。`flags` 为五位异常标志，位定义见 [共同契约](../contract.md)。只有整数除法使用 `remainder`；浮点结果将其置零。`io.in` 与 `io.out` 都是 Chisel `Decoupled` 接口，握手条件分别为 `io.in.valid && io.in.ready` 和 `io.out.valid && io.out.ready`。

`UnitIO` 还提供同步 `flush` 以及四个观察端口：

- `stageValid`：弹性单元中每一级的有效位；迭代单元中当前阶段的一位有效编码。
- `occupancy`：弹性单元的有效级数量，或迭代单元的 0/1 在途标记。
- `phase`：迭代模块当前阶段；弹性模块恒为 0。
- `iteration`：当前阶段之前的 `iterate` 阶段数量；弹性模块恒为 0。

## ArithmeticModule

`ArithmeticModule` 把 `reset` 和 `io.flush` 合成 `cancel`。`cancel` 为真时，派生控制器清除状态，并且本拍不允许输入或输出握手。

该基类保存上一拍被输出背压时的 payload。在未取消的下一拍，它断言 `io.out.valid` 仍为真且 `io.out.bits` 不变。因此上游模块不能只依赖 `Decoupled` 类型而忽略输出稳定性要求。

## ElasticModule

`ElasticModule` 用每级一个 `valid` 寄存器实现深度等于共同契约 `latency` 的流水线。第 `i` 级的推进条件为：该级为空，或下一等级允许推进；末级的下一等级由 `io.out.ready` 决定。每级只在自身允许推进且上游有效时更新 payload 寄存器。

该规则允许流水线中的空洞向前移动，也允许末级交付旧结果的同一拍接收新请求。无背压时，第 `k` 拍接收的请求于第 `k+latency` 拍交付；所有当前弹性算术单元的启动间隔为 1。下游背压会逐级传递至 `io.in.ready`，但被阻塞的输出保持稳定。

`stage(next, index)` 为实际中间数据创建寄存器，数据有效性由对应 `valid(index)` 决定。各运算模块只在共同契约规定的阶段调用该函数；因此改变流水切分必须同时更新契约和 Python 周期模型。

## IterativeModule

`IterativeModule` 只有一个请求槽。`phase=0` 表示空闲；输入握手将它置为 1；之后每拍加一，直到 `phase=latency` 使响应有效。响应被背压时阶段保持不变。交付旧响应的同拍可接收新请求，因此无背压启动间隔等于 `latency`。

每项除法的阶段名来自共同契约。例如 FP32 除法的阶段为 `decode`、`normalize`、13 个 `iterate`、`correct`、`round_normalize`、`grs` 和 `round`。`iteration` 不是独立计数器，而是由已完成的阶段名计算，便于 Python/RTL 对齐测试定位迭代位置。

## 复位与清空

时钟复位和 `flush` 有相同的传输优先级：观察端口仍可显示提交前的旧状态，但 `in_ready` 和 `out_valid` 均为低；提交时清除有效位或阶段状态。数据寄存器不要求写零，只有有效位或阶段决定其中的数据是否可用。Python 周期模型采用相同规则。
