# 整数算术模块

这一章描述 `Integer.scala` 中的 `IntAdd`、`IntMul` 和 `IntDiv`。三个模块都接收同宽 `a/b`、返回低 `W` 位结果，并由构造参数 `signed` 选择二进制补码有符号或无符号语义。数值边界和异常标志定义见 [共同契约](../contract.md)。

## IntAdd

`IntAdd(w, signed, s)` 是一拍 `ElasticModule`。三个宽度采用不同的进位网络：

| 宽度 | 实现 | 进位组织 |
|---|---|---|
| INT8 | `a +& b` | Chisel 普通加法链 |
| INT16 | `Adders.grouped` | 四位分组的产生/传递逻辑 |
| INT32 | `Adders.brentKung` | Brent–Kung 前缀网络 |

`Adders.brentKung` 首先为每一位生成 `(generate, propagate)` 对，再做上行和下行前缀组合，最后产生进位与和。`Adders.grouped` 在每个四位边界重用已算出的组内产生/传递项。文档中的“普通加法链”只描述当前 `INT8` 源码选择，不承诺综合后的门级映射。

结果取 `sum(W-1, 0)`。有符号模式在两个输入符号相同且结果符号改变时置 `OF`；无符号模式在扩展和的最高位为 1 时置 `OF`。加法器不设置 `NX`。

## IntMul

`IntMul(w, signed, s)` 是弹性流水线。它先生成部分积，再按 Dadda 目标高度逐列压缩，最后把两行压缩结果相加。`Compressors.reduce` 在同一位列中使用 3:2 全加器或 2:2 半加器，把进位送到下一位列；`Compressors.targets` 生成 Dadda 高度序列。

| 宽度 | 部分积 | 契约阶段 | 延迟 |
|---|---|---|---:|
| INT8 | `Compressors.baugh` | `compress_0 → carry_propagate` | 2 |
| INT16 | `Compressors.booth` | `compress_0 → carry_propagate` | 2 |
| INT32 | `Compressors.booth` | `compress_0 → compress_1 → carry_propagate` | 3 |

INT8 使用 Baugh–Wooley 形式的有符号修正位；`signed=false` 时同一生成函数省略该修正。INT16/32 使用 radix-4 Booth 编码，将乘数按三位重叠窗口重编码为 `0`、`±a` 或 `±2a`，从而减少部分积行数。每个压缩区间结束处通过 `stage()` 保存部分积和 tag，因此背压时数据与有效位一起停住。

最终结果保留低 `W` 位。对于有符号乘法，若高 `W` 位并非结果符号位的重复，置 `OF`；无符号乘法只要高 `W` 位非零即置 `OF`。

## IntDiv

`IntDiv(w, signed, s)` 是单请求 `IterativeModule`。输入握手时，它保存操作数、tag、被除数原值和符号信息；结果阶段之前不会接受另一笔请求。特殊输入也走完整的阶段序列，不会缩短外部可见延迟。

### INT8：radix-2 非恢复除法

INT8 在每个 `iterate` 阶段将部分余数左移并引入被除数的一位。根据旧余数符号选择减除数或加除数，再将新余数的符号作为当前商位。累计八次后，`correct` 阶段把负余数加回除数并写出商、余数。

### INT16/INT32：radix-4 SRT

INT16/INT32 的 `normalize` 阶段左移除数到最高位，并对齐被除数；`seed` 阶段生成第一个商数字及初始余数。每个 `iterate` 阶段从 `{−2, −1, 0, 1, 2}` 选择冗余 radix-4 商数字。实现预先保存 `±d`、`±3d`，同时计算四个候选余数，然后按所选商数字更新余数。

正商数字和负商数字分别累积在 `positive`、`negativeDigits`。`correct` 阶段使用 Brent–Kung 网络完成两者转换，并在余数为负时修正余数。最后 `sign` 阶段恢复商和余数符号。

| 宽度 | 阶段 | 延迟 |
|---|---|---:|
| INT8 | `decode → 8×iterate → correct` | 10 |
| INT16 | `decode → normalize → seed → 8×iterate → correct → sign` | 13 |
| INT32 | `decode → normalize → seed → 16×iterate → correct → sign` | 21 |

除零时模块在最终输出阶段返回全一商、原始被除数余数和 `DZ`。有符号最小负数除以 −1 返回最小负数、零余数和 `OF`。这些结果由保存的 `dz`、`ov`、`original` 和符号寄存器覆盖正常迭代结果。
