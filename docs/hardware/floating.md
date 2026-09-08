# 浮点加法、乘法与 FMA

这一章描述 `Floating.scala` 的公共数据表示和 `FpAdd`、`FpMul`、`FpFma`。它们派生自 `FloatingElasticModule`，使用 `ElasticModule` 的逐级有效位控制，因此在无背压下都能每拍接收一笔请求。BF16 使用 1/8/7 编码和 binary32 相同的指数范围；格式编码、特殊值与标志语义由 [共同契约](../contract.md) 定义。

## 公共数据表示

`FloatLogic.decode` 把输入编码解为 `Decoded`：符号 `sign`、规范化有效数 `sig`、有效数最低位的二进制指数 `exp`，以及 `zero`、`inf`、`nan`、`snan` 分类位。对次正规数，解码器通过前导零检测移位有效数，并同步减小 `exp`；后续数据通路不需要为正规数和次正规数维护两套对齐路径。

`Meta` 保存特殊结果、异常标志、舍入码和 tag。`FloatLogic.special` 在算术数据通路开始前判断 NaN、无效操作、除零和无穷输入。特殊结果仍随普通流水级传输，外部可见延迟不因特殊输入缩短。

普通有限结果在 `Magnitude`、`Normalized` 与 `Prepared` 间传递：

- `Magnitude` 保存未舍入绝对值、指数、符号和 `Meta`。
- `Normalized` 保存最高有效位位置、量化指数 `quantum` 和取窗口位置 `cut`。
- `Prepared` 保存目标有效数加 Guard/Round/Sticky 位的窗口。

`rightJam` 在右移时把被丢弃位归并到最低 sticky 位；`alignOne` 用它完成带饱和移位量的有效数对齐。`normalize` 使用优先编码器找最高有效位，`prepare` 形成包含 GRS 位的舍入窗口，`finish` 依据五种舍入码产生结果、溢出和舍入后微小标志。

## 舍入与特殊结果

`finish` 从窗口中取目标有效数、guard 和 sticky。RNE 在 guard 为 1 且 sticky 或目标有效数最低位为 1 时加一；RMM、RDN、RUP 使用相应方向规则。发生进位时有效数右移一位并调整指数。

模块对 IEEE 格式的溢出按舍入方向选择无穷或最大有限数；E4M3FN 和 E2M1 使用有限格式的饱和规则。微小标志采用目标精度舍入后的判定。特殊路径在 `Meta.special` 为真时覆盖普通编码和标志。完整边界定义以及规范 NaN 编码见 [共同契约](../contract.md#浮点编码)。

## FpAdd

`FpAdd` 先解码两个操作数并形成 `Meta`，再把两个有效数对齐到共同指数。相同符号时相加，不同符号时以绝对值较大者减较小者；精确抵消时，RDN 返回负零，其余模式返回正零，两个同号零相加保持原符号。

FP32 的 `near_far` 变体在异号且指数差不超过一时选择近路径，使用固定移位；其他输入走带 sticky 的远路径移位器。FP16、BF16 均采用 `single_path`，FP8 也使用单路径但省去较深的解码寄存阶段。求和后统一执行规格化、GRS 准备和编码。

| 格式 | 阶段 | 延迟 |
|---|---|---:|
| FP32 | `decode → near_far_align → add → normalize → grs → round` | 6 |
| FP16 | `decode → single_align → add → normalize → grs → round` | 6 |
| BF16 | `decode → align → add → round_normalize → grs → round` | 6 |
| E4M3FN/E5M2 | `align → add_normalize → grs → round` | 4 |

## FpMul

`FpMul` 对解码有效数产生部分积，符号为两个输入符号异或，指数为两个最低位指数之和。FP32 的有效数精度满足 `p >= 16`，使用 `Compressors.booth` 生成 radix-4 Booth 部分积；FP16、BF16 与 FP8 使用直接部分积。随后按照 Dadda 目标高度压缩，流水寄存器切在压缩区间之间。

压缩后的两行在结果阶段相加，形成 `Magnitude` 并送入公共规格化和舍入路径。`Meta` 与符号、指数同拍寄存，因此特殊结果、tag 和普通乘积不会错配。

| 格式 | 阶段 | 延迟 |
|---|---|---:|
| FP32/FP16 | `decode → compress_0 → compress_1 → product → normalize → grs → round` | 7 |
| BF16 | `compress → product_normalize → grs → round` | 4 |
| E4M3FN/E5M2 | `compress_0 → product_normalize → grs → round` | 4 |

## FpFma

`FpFma` 计算 `a × b + c`。与“先乘后舍入、再加”的串接不同，乘积在部分积压缩完成前保留，`c` 的有效数在最终进位传播之前进入同一压缩网络，随后只执行一次公共舍入。

对于较深的 FP32、FP16 和 BF16 配置，解码寄存后先完成一部分乘积压缩，再寄存剩余行、乘积指数、乘积符号、`c` 和 `Meta`。乘积与 `c` 的最高有效位用于判断哪一项主导，并选择对齐基准。若 `c` 比乘积大到低位不再影响目标格式舍入，乘积被归并为 sticky 信息；其余情况保留对齐后的两行乘积。异号相加使用补码形式送入压缩树，最后一次进位传播生成绝对值与结果符号。

小格式为缩短关键路径，当前源码同时形成负幅值候选；FP32/FP16 保留经面积/时序测量选择的串行绝对值路径。该差异不改变外部格式或周期契约。

| 格式 | 阶段 | 延迟 |
|---|---|---:|
| FP32/FP16 | `decode → compress_0 → compress_1 → align_compress → add → normalize → grs → round` | 8 |
| BF16 | `decode → compress → product_compress → align_compress → add → round_normalize → grs → round` | 8 |
| E4M3FN/E5M2 | `product_compress → align_compress → add_normalize → grs → round` | 5 |

## 流水阻塞

三个模块都在共同契约列出的阶段保存实际中间数据。任一后级或输出被阻塞时，`ElasticModule` 同时停住对应数据与有效位；结果到达末级后保持 `out.valid`、`bits`、`flags`、`tag` 和 `remainder` 稳定，直到 `out.ready` 完成握手。具体控制规则见 [接口与流水控制](interface.md)。
