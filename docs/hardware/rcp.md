# 倒数 rcp

`FpRcp` 计算同格式的 $1/x$，支持 FP32、FP16、BF16 和五种舍入方式。Python 提供 `FpRcp(format="fp32")`、`FP32Rcp`、`FP16Rcp`、`BF16Rcp`；Chisel 通过 `Arithmetic("fp32", "rcp")` 集成。

## 规格化与指数

分类逻辑把有限非零输入表示为 $x=(-1)^s m2^e$，其中 $1\le m<2$。次正规输入先左移有效数并修正指数。有效数通路计算 `1/m`，指数通路计算 `-e`，符号沿用输入符号。

输出通过公共规格化、次正规移位和舍入逻辑生成。保留的有效数位与精确余量共同决定五种模式的舍入方向以及 NX。次正规输出在移位后统一舍入，避免重复舍入。

## 初值和空间展开的迭代

`newton_residual` 变体用有效数高位选择初值表，按下式修正：

$$
y_{i+1}=y_i(2-my_i).
$$

表项与中间结果使用显式定点位宽；每次乘法保留完整乘积，再在契约规定的位置右移。`refinement` 记录表地址宽度、小数位数和迭代次数。每轮迭代拥有独立乘法资源，`multiply_stages` 控制内部压缩树的流水切分。

`goldschmidt` 变体同时保存 `y` 和 `d=my`，使用 `2-d` 并行修正两者。它与 Newton 通路共用初值表格式和最终校正规则。

## 精确余量校正

设目标格式有 `fb` 个尾数字段位，校正通路取 `Q=fb+4` 个小数位。近似结果截断为整数候选 `q` 后，计算完整整数乘积 `m_sig × q`，并与 `2^(Q+fb)` 比较。

规格化有效数穷举检查候选与精确截断值的距离至多为 1。硬件分别判断 `q`、`q+1` 所在的一侧，必要时固定执行一次加一或减一。校正后的乘积等于目标时结果精确；否则设置 sticky 位。公共浮点舍入通路随后处理 RNE、RTZ、RDN、RUP 和 RMM。

## 查表与展开除法变体

`normalized_table` 为每个规格化有效数保存未舍入商位和余量非零标志。BF16 表有 128 项，FP16 表有 1,024 项。指数恢复与次正规处理仍在表之后完成。

`reciprocal_digits` 从常分子 1 开始，完全展开商位生成步骤。每一步比较余数和除数，选择减法结果，再左移进入下一步。`root_bits_per_stage` 指定每级组合的商位数。这些实现使用相同的接口、数值规则和 II=1 流水控制。

`division_lookahead=true` 的变体每级生成两位商。预处理级保存 `3m`，各商位级并行计算 `2R−m`、`2R−2m`、`2R−3m`。三个差值的符号确定本级的两位商，选中的非负余量左移一位进入下一流水级。它增加一个除数预处理级和并行减法资源，缩短相邻商位的组合依赖。

默认变体及阶段列表由[共同契约](../../src/zircon_asic/data/contract.json)指定。表、初值和定点参数见 [`sfu.json`](../../src/zircon_asic/data/sfu.json)，面积、延迟及选型结果见[配置清单](../../reports/CONFIGURATIONS.md)。

## 接口与特殊值

```python
from zircon_asic import FP32Rcp, Request, Rounding

unit = FP32Rcp()
result = unit.compute(Request(a=0x40400000, rounding=Rounding.RNE))
assert result.bits == 0x3EAAAAAB  # 最近偶数舍入的 1/3
```

只使用 `a`；`b/c` 仍检查位宽，`remainder=0`，标签不变。`compute_batch(a)` 支持一元批量调用。

| 输入 | 结果 | 异常 |
|---|---|---|
| `+0`、`-0` | 同符号 Inf | DZ |
| `+Inf`、`-Inf` | 同符号零 | 无 |
| quiet NaN | 规范化 quiet NaN | 无 |
| signaling NaN | 规范化 quiet NaN | NV |
| 有限非零数 | 正确舍入的倒数 | 按精确余量报告 NX，按舍入结果报告 OF／UF |

每级容量为 1，无背压启动间隔为 1。特殊值使用完整延迟；背压时输出全部字段保持稳定。复位和清空取消在途请求并优先于握手。功能计算不推进周期，周期接口与[其他算术模块](interface.md)相同。

初值与迭代结构可参考 [Tenstorrent reciprocal](https://github.com/tenstorrent/tt-metal/blob/main/tt_metal/hw/ckernels/wormhole_b0/metal/llk_api/llk_sfpu/ckernel_sfpu_recip.h)；近似与余量校正的分工可参考[倒数和平方根校正研究](https://arxiv.org/html/2404.00387v1)。
