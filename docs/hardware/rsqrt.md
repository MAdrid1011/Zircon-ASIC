# 倒平方根 rsqrt

`FpRsqrt` 计算同格式的 $1/\sqrt{x}$，支持 FP32、FP16、BF16 和五种舍入方式。Python 提供 `FpRsqrt` 及 `FP32Rsqrt`、`FP16Rsqrt`、`BF16Rsqrt`；Chisel 使用 `Arithmetic(format, "rsqrt")`。

## 指数奇偶与初值

解码后把指数奇偶并入有效数，得到 $x=m'2^{2q}$，其中 $1\le m'<4$。有效数通路计算 $1/\sqrt{m'}$，输出指数调整为 `-q`。初值表地址由指数奇偶位和有效数高位组成，分别覆盖 `[1,2)` 与 `[2,4)`。

Newton 变体按下式展开：

$$
y_{i+1}=y_i(3-m'y_i^2)/2.
$$

每轮依次计算平方、乘以输入有效数和修正乘积。除以 2 合入最后一次定点移位。各轮拥有独立资源，流水切分由契约中的 `multiply_stages` 指定。

`prefix_factor` 选择用前缀加法器生成修正因子 `3−m'y²`，然后进入下一乘法压缩树。它保持相同的定点位宽和截断位置。

Goldschmidt 变体同时维护平方根近似 `d=m'y` 和倒平方根近似 `y`，计算修正因子 `(3-dy)/2`，再并行更新 `d`、`y`。最终结果独立舍入。

## 精确平方比较

校正阶段取 `Q=fb+4` 个小数位，将未舍入近似值转为整数候选 `q`。完整乘积 `m_sig × q²` 与 `2^(2Q+fb)` 比较。相邻候选的乘积差为：

$$
m_{sig}(q+1)^2-m_{sig}q^2=m_{sig}(2q+1).
$$

规格化有效数与两种指数奇偶情况的穷举检查保证候选距离精确截断值至多为 1。硬件使用上述乘积差执行固定的一次校正；精确相等时清除 NX，否则保留 sticky 位。保留的根位与 sticky 位进入公共舍入通路，决定五种舍入方式的输出。

该比较保留完整整数精度，直接决定倒平方根的舍入，避免先舍入平方根产生的双重舍入。`exactness_classification` 变体利用二进制有理数的性质判断余量是否为零：规格化有效数必须等于 1，且指数必须为偶数；候选值的大小校正仍采用完整整数乘积比较。

## 完整有效数表

`normalized_table` 变体保存所有规格化有效数及指数奇偶的未舍入结果和精确性信息。BF16 为 256 项，FP16 为 2,048 项。表项通过独立整数平方比较生成，输出指数恢复和浮点打包由公共通路完成。

默认算法、表规模和实际流水级数见[共同契约](../../src/zircon_asic/data/contract.json)及 [`sfu.json`](../../src/zircon_asic/data/sfu.json)。[配置清单](../../reports/CONFIGURATIONS.md)记录相应物理结果。

## 接口与异常

```python
from zircon_asic import BF16Rsqrt, Request

unit = BF16Rsqrt()
result = unit.compute(Request(a=0x4080))  # 1/sqrt(4)
assert result.bits == 0x3F00             # 0.5
```

输入只使用 `a`，其余操作数字段仍检查位宽。输出 `remainder=0`，标签原样传递。

| 输入 | 结果 | 异常 |
|---|---|---|
| `+0` | `+Inf` | DZ |
| `-0` | `-Inf` | DZ |
| `+Inf` | `+0` | 无 |
| 负有限非零数、`-Inf` | 规范化 quiet NaN | NV |
| quiet NaN | 规范化 quiet NaN | 无 |
| signaling NaN | 规范化 quiet NaN | NV |
| 正有限非零数 | 正确舍入结果 | 精确余量非零时 NX |

三种格式的正有限非零输入产生正规有限结果。数值参考直接调用 MPFR `rec_sqrt`，并按本库规则将 `-0` 转换为 `-Inf`、DZ；参见 [MPFR 数学函数](https://www.mpfr.org/mpfr-current/mpfr.html)。

## 流水与组合

无背压时每拍接收一笔请求，第 `k` 拍接收的结果在第 `k+L` 拍交付。每级容量为 1，背压按弹性流水规则传播，阻塞输出保持稳定。特殊值经过完整流水。复位和清空优先于握手，取消在途请求。

[组合示例](../../examples/nonlinear_spm.py)提供 SPM → rsqrt → 乘法 → SPM。乘法器在算子边界接收已按目标格式舍入的 rsqrt 结果；调用者可以在周期边界切换 Python／Numba 后端。
