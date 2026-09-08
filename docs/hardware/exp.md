# 指数函数 exp

`FpExp` 接收一个 FP32、FP16 或 BF16 编码，计算同格式的自然指数 $e^x$。Python 的 `FpExp(format="fp32")` 和 `FP32Exp`、`FP16Exp`、`BF16Exp` 使用同一数值规则。Chisel 通过 `Arithmetic("fp32", "exp")` 实例化。

## 数值接口

输入取自 `Request.a`；`b`、`c` 默认是 0，仍检查字段位宽，其数值不参与运算。`rounding` 只接受 `RNE`。Python 在推进状态前拒绝其他模式，Chisel 在请求握手时检查。输出使用原有 `Response`，`remainder` 固定为 0，标签原样传递。

```python
from zircon_asic import FP32Exp, Inputs, Request

unit = FP32Exp()
value = unit.compute(Request(a=0x3F800000))  # exp(1)
batch = unit.compute_batch([0, 0x3F800000], backend="python")
assert batch.bits[0] == 0x3F800000
assert unit.step(Inputs(Request(a=0x3F800000, tag=7))).accepted
```

有限范围内的结果是夹住数学真值的两个相邻浮点数之一，误差严格小于这两个数之间的间距。次正规区采用固定最小间距。数学结果可精确表示时返回该值；输出单调不减。内部近似结果在打包时采用最近偶数舍入。Python、Numba 和 RTL 返回的编码与异常标志相同。

## 范围缩减

分类逻辑首先识别 NaN、无穷、零及类别阈值。有限输入被解码为符号、整数有效数和二进制指数。有效数与量化后的 `log2(e)` 常数相乘，移位得到定点数 `t`。负输入通过保留丢弃位信息实现向下取整。

带幅值表的通路将 `t` 拆分为：

$$
t=n+j/2^k+r,\qquad 0\le r<2^{-k}.
$$

`n` 用于输出指数恢复，`j` 选择幅值表 $2^{j/2^k}$，`r` 进入多项式。`range_stages=2` 时，移位与符号修正分别寄存；这项切分不改变定点运算的截断位置。

无幅值表的 `polynomial` 变体使用以零为中心的余量。硬件把余量偏置到 `[0,1)`，计算 $2^{u-1/2}$，以正系数 Horner 通路保持单调性。

## 查表与多项式

`exp.fraction_bits` 指定内部小数位数，`table_bits` 指定表地址位数，`degree` 指定多项式次数。Horner 每一步计算 `coefficient + floor(product / 2^fraction_bits)`；系数加数在乘法压缩树中融合，右移前保留完整乘积。幅值表与多项式结果相乘后再次按契约截断。

`multiply_stages` 控制乘法内部的寄存切分。多级实现把矩形部分积压缩到两行，再用前缀加法器生成完整乘积。每一步拥有独立硬件资源，可以每拍接收一笔请求。

系数由 Sollya 8.0 的 `fpminimax` 生成，`checkinfnorm` 检查多项式误差界。误差计算还计入常数乘法、范围缩减、表量化及每次定点截断。分段连接点通过整数计算检查顺序；段内正系数与单调截断保证单调性。参数、系数、表和阈值位于随包分发的 [`sfu.json`](../../src/zircon_asic/data/sfu.json)。

## 指数恢复与异常

多项式结果与 `n` 一起进入规格化、保护位／舍入位／sticky 位生成和最终打包。独立 MPFR 参考生成零、正规／次正规交界及溢出阈值，输出类别与数学真值按 RNE 舍入后的类别一致。

| 输入 | 结果 | 异常 |
|---|---|---|
| `+0`、`-0` | `1` | 无 |
| `+Inf` | `+Inf` | 无 |
| `-Inf` | `+0` | 无 |
| quiet NaN | 规范化 quiet NaN | 无 |
| signaling NaN | 规范化 quiet NaN | NV |
| 有限非零数 | 指数结果 | NX；溢出时加 OF，微小时加 UF |

## 流水与组合

所有特殊输入通过完整流水，延迟与普通输入相同。每级容量为 1；下游阻塞时，级有效位和对应数据按弹性流水规则保持。输出阻塞期间编码、标志、标签与 `remainder` 稳定。复位和 `flush` 抑制本拍握手并取消在途请求。

`describe()["latency"]` 返回默认配置的完整延迟。第 `k` 拍接收的请求在无背压时于第 `k+L` 拍交付，启动间隔为 1。阶段名称和容量以[共同契约](../../src/zircon_asic/data/contract.json)为准，实测面积与时序见[配置清单](../../reports/CONFIGURATIONS.md)。

[SPM 组合示例](../../examples/nonlinear_spm.py)展示读 SPM、计算 exp 和写回另一个 SPM。地址由调用者提供，标签携带显式写回地址。

算法背景见 [Tenstorrent exp 实现](https://github.com/tenstorrent/tt-metal/blob/main/tt_metal/hw/ckernels/wormhole_b0/metal/llk_api/llk_sfpu/ckernel_sfpu_exp.h)和 [Sollya 误差界检查](https://sollya.org/sollya-8.0/help.php?name=checkinfnorm)。
