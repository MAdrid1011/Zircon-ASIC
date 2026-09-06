# Python 模拟器结构

这一章描述 Python 包的功能计算、周期状态和内置网络执行。Chisel 数据通路的模块级说明位于 [硬件架构总览](hardware/README.md)；位编码、舍入和周期定义位于 [共同契约](contract.md)。

## 算术对象

`ArithmeticUnit` 是所有 Python 算术对象的共同基类。它提供：

- `compute(request)`：纯功能计算，不改变周期状态。
- `compute_batch(...)`：支持 NumPy 广播的批量计算。
- `eval(inputs)`、`tick()`、`step(inputs)`：观察、提交和单拍推进。
- `reset()`、`flush()`、`describe()`：状态管理与配置/验证信息查询。

`FloatingPointUnit` 用 `float_compute` 实现 FP32、FP16、E4M3FN、E5M2 的加、乘、FMA 和除法；E2M1 使用独立的 `fp4_compute` 查表路径。`IntegerUnit` 用 `int_compute` 实现 INT8/16/32 的有符号或无符号加、乘、除法。`FP32Fma`、`FP16Add`、`INT8Div` 等名称是这些参数化类的便捷封装。

功能路径只使用整数操作，不依赖宿主浮点数。浮点路径解码原始位编码，使用整数有效数和二进制指数做精确对齐、乘积、商余、规格化与舍入；整数路径保留完整中间结果来判断溢出。详细数值规则见 [共同契约](contract.md)。

## 周期状态

构造默认单元时，`timing_for(format, op)` 从共同契约读取 `latency`、`kind`、`phases` 和每级容量。`ArithmeticUnit` 保存 `cycle`、统计信息、在途槽位和迭代阶段。

弹性模块在 `_slots` 中保存每个流水级的 `Response`。`eval()` 从旧状态逆向计算 ready，并报告 `stage_valid` 和 `occupancy`；`tick()` 在所有握手确定后统一移动槽位。迭代模块只保存一笔 `Response` 和 `_phase`，其状态变化与 Chisel `IterativeModule` 相同。

`step()` 等价于连续执行一次 `eval()` 与一次 `tick()`。返回的 `Outputs` 包含：

| 字段 | 含义 |
|---|---|
| `in_ready`、`out_valid` | 本拍输入/输出端口状态 |
| `accepted`、`delivered` | 本拍输入/输出握手 |
| `response` | 输出有效时的结果与 tag |
| `occupancy`、`stage_valid` | 提交前的在途状态 |
| `phase`、`iteration` | 迭代模块的阶段位置 |

`events` 设为列表后，模块会在每个 `tick()` 记录当前周期、输入和输出。调用者也可在调用 `step()` 前读取 `unit.cycle`，通过 tag 建立每笔事务的接收、就绪与交付时间。复位和清空优先于正常传输，行为与 [共同契约的周期推进](contract.md#周期推进) 一致。

## 批量与 Numba

`compute_batch` 支持标量和 NumPy 数组广播，返回 `BatchResponse(bits, flags, tags, remainder)`。`backend="python"` 强制纯 Python；`backend="numba"` 使用编译内核；`backend="auto"` 在 Numba 可用时选择它，否则回退到 Python。

Numba 内核不调用宿主浮点数，也不启用 `fastmath`。FP32 FMA 使用固定数量的 32 位 limb 保存跨大指数差的精确中间值；FP4 使用预生成的打包表。加速路径与纯 Python 路径共享位级语义，并由测试逐位比较。

## Network

`Network` 连接算术 `ArithmeticUnit`、`FIFO`、`DelayLine` 和多端口 `SPM`。`source`、`sink` 使用 `port` 选择端口；`connect` 使用 `source_port` 和 `destination_port`，默认均为 0。端口 0 的结果以模块名为键，其他端口以 `(name, port)` 为键，交付记录仍为 `(cycle, response)`。

每拍先快照所有模块的旧状态输出，再构造请求载荷，随后按逆拓扑计算下游 ready 与完整 SPM 仲裁，最后统一提交每个模块一次。SPM 的全部端口由同一个状态对象提交。`InputSource` 仅在输入握手后前进，模块添加顺序不影响结果。

普通算术连接将上游 `bits/tag` 映射到下游 `a/tag`，并可指定 `b`、`c`、`rounding` 常量。`mapping` 用 `Field("bits")`、`Field("tag")` 等字段引用和整数常量描述载荷；写入 SPM 时显式提供地址、写操作和数据映射。成功读响应可以进入算术通路；写确认与地址错误由响应接收器或用户控制器处理。SPM 保存原始比特，算术格式转换由调用方表达。

每个端口连接一个来源和一个目的地，模块依赖图须无环。多端口仲裁按同一模块处理依赖；广播、汇合和反馈架构由用户的时钟状态控制器驱动。示例见 [算术网络](../examples/network.py)、[存储与算术组合](../examples/spm.py) 和 [用户控制器](../examples/spm_controller.py)。

`Network.run(..., backend="numba")` 在编译循环中执行内置组件与静态字段映射。连续数组保存算术流水、FIFO、SPM 存储和响应队列、轮询指针、源位置及统计，调用结束后写回 Python 状态。可以在完整周期边界切换 Python 与 Numba。编译后端遇到不支持的连接会报错；可用映射由字段引用和常量组成。

## SPM 对象

`CycleModule` 定义共同周期协议，算术模块和 SPM 分别实现。`SPM.compute(request)` 按当前内容执行有状态读写，`compute_batch` 按输入顺序执行批量访问；二者均不推进周期。功能调用及镜像加载、导出要求模块无在途事务和待提交求值。

单端口使用 `Inputs(MemoryRequest(...))`，多端口使用等长 `Inputs` 元组求值，并统一 `tick()`。响应类型为 `MemoryResponse(bits, tag, write, status)`。寻址、初始化、错误状态和两拍控制规则见 [SPM](hardware/spm.md)。
