# 传输与组合模块

这一章描述 `Transport.scala` 的 `DelayLine`、`StreamFIFO` 和 `NetworkExample`。它们为上层架构提供与算术单元相同的 `valid/ready` 和 `flush` 行为，但并不改变算术数值。

## DelayLine

`DelayLine(w, cycles)` 是一个 `ElasticModule`。它把输入 `Request` 重新解释为传输 payload：`a` 承载结果 `bits`，`b(4,0)` 承载 `flags`，`c` 承载 `remainder`，`tag` 原样传递。每一级都保存完整 `Response`，因此输出会保留所有响应字段。

延迟线有 `cycles` 个容量为一的弹性阶段。它可以用于模型化寄存器切分、固定互联延迟或接收端流水。被下游背压时，其末级 payload 和有效位保持不变；空洞可在后续拍被填补。

## StreamFIFO

`StreamFIFO(w, depth)` 是非直通环形 FIFO。状态为 `memory`、读指针 `head`、写指针 `tail` 和条目计数 `count`。每个条目为一个完整 `Response`，输入的字段映射与 `DelayLine` 相同。

当 `count != 0` 时输出有效；当 FIFO 未满，或本拍同时发生输出握手时，输入 ready。因而满 FIFO 可以在同一拍出队旧条目、入队新条目。空 FIFO 接收输入后，最早下一拍输出有效，不提供组合旁路。

`flush` 或复位时，FIFO 把 `head`、`tail`、`count` 清零。存储阵列内容不作为有效状态。`stageValid` 依据 `count` 生成连续的逻辑占用位，用于观察深度和占用，不表示物理存储地址顺序。

## NetworkExample

`NetworkExample` 是一个具体的 8 位 Chisel 组合示例：

```text
Int8Add → StreamFIFO(depth=3) → Int8Mul(b=3) → DelayLine(cycles=2)
```

它手工实现了相邻模块的 `valid/ready` 连接。进入传输模块时转发 `bits/flags/remainder/tag`；进入乘法器时把上游 `bits` 作为 `a`，常数 3 作为 `b`，并把 `tag` 继续传递。这个例子演示握手与占用的组合，不是任意类型算术结果间自动转换的通用网络。

顶层把四个子模块的 `flush` 并联，输出末级响应，并拼接末级到首级的 `stageValid`。`occupancy` 为四个子模块占用的加和。上层设计如果需要多输入汇合、广播、仲裁、跨宽度转换或异常标志合并，应显式定义这些部件和字段语义。

## Python Network 的范围

Python `Network` 遵循相同的“先观察、后统一提交”周期规则，支持单流有向链和多条独立链，并可把内置算术、FIFO 和延迟线交给 Numba 执行。它会拒绝隐式广播、多驱动和环路。该限制防止未定义的同拍仲裁规则被错误地隐藏在 API 中；复杂架构需要由调用者实现明确的 router、join、scoreboard 或存储器模型。
