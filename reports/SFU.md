# 非线性算子验证记录

FP32、FP16、BF16 的 exp、rcp、sqrt、rsqrt 使用独立整数数值内核、Numba 内核与 Chisel 流水实现。
共同契约 SHA-256：`9208c9d6f1f231938419555263f2c0417528b0fa3d7e65db46976f49dc985ffa`。
表、系数与阈值 SHA-256：`3797473232beb0d9cc02baf841ccb6cbb796a06da8cfd44ac7dba761cfbbf05a`。

## 数值与周期

| 配置 | 数值输入／舍入组合 | 精度 | 三方回归拍数 | L / II |
|---|---:|---|---:|---:|
| fp32.exp | 1,050,932 | <1 ULP、RNE 打包 | 200,000 | 17 / 1 |
| fp32.rcp | 5,281,060 | 五种模式正确舍入 | 200,000 | 20 / 1 |
| fp32.sqrt | 5,276,310 | 五种模式正确舍入 | 200,000 | 20 / 1 |
| fp32.rsqrt | 5,276,120 | 五种模式正确舍入 | 200,000 | 34 / 1 |
| fp16.exp | 65,536 | <1 ULP、RNE 打包 | 200,000 | 10 / 1 |
| fp16.rcp | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |
| fp16.sqrt | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |
| fp16.rsqrt | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |
| bf16.exp | 65,536 | <1 ULP、RNE 打包 | 200,000 | 10 / 1 |
| bf16.rcp | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |
| bf16.sqrt | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |
| bf16.rsqrt | 327,680 | 五种模式正确舍入 | 200,000 | 5 / 1 |

exp 使用 Sollya 8.0 区间误差界、逐级定点截断误差累积和分段单调性检查；类别阈值由自适应 MPFR 有向上下界生成。三个精确算子以 MPFR 为数值参考，FP32 规格化通路穷举有效数与相应指数奇偶组合。

逐周期比较请求与响应握手、结果、异常、标签、级有效位、占用和事务事件。每项完整回归包含五个固定种子各 20,000 拍及一组 100,000 拍长回归。

## ASIC 面积与时序

ASAP7 RVT，TC，0.70 V、0 °C，周期 1,000 ps，输入／输出延迟 200 ps，时钟不确定度 50 ps。完整模块包含查表、校正逻辑、弹性寄存器和握手控制。

| 配置 | 结构 | 面积 µm² | setup / hold ps | 物理阶段 |
|---|---|---:|---:|---|
| fp32.exp | table_polynomial | 2898.800 | 70.833 / 5.186 | detailed-route |
| fp32.rcp | reciprocal_digits | 2140.990 | 42.968 / 1.011 | detailed-route |
| fp32.sqrt | nonrestoring | 2165.230 | 54.543 / 0.775 | detailed-route |
| fp32.rsqrt | newton_residual | 8263.710 | 38.366 / 0.710 | detailed-route |
| fp16.exp | table_polynomial | 996.178 | 34.120 / 1.181 | detailed-route |
| fp16.rcp | normalized_table | 411.666 | 276.798 / 1.278 | detailed-route |
| fp16.sqrt | normalized_table | 530.304 | 237.141 / 0.681 | detailed-route |
| fp16.rsqrt | normalized_table | 512.691 | 168.594 / 1.023 | detailed-route |
| bf16.exp | table_polynomial | 911.512 | 37.511 / 0.926 | detailed-route |
| bf16.rcp | normalized_table | 290.404 | 183.088 / 1.120 | detailed-route |
| bf16.sqrt | normalized_table | 300.581 | 258.089 / 0.675 | detailed-route |
| bf16.rsqrt | normalized_table | 309.898 | 181.964 / 1.347 | detailed-route |

候选先通过数值筛选，再比较综合与全局布线；每项至多两个候选进入详细布线。通过时序、约束、时钟与路由检查后，按面积×延迟选型，差异不足 5% 时优先较小面积。

布线网表使用对应 Liberty 生成的功能单元模型，回放完整数值向量和六组逐周期激励；网表、RTL、激励与工艺文件均记录 SHA-256。

## CPU 与组合网络

| 配置 | 路径 | Python 秒 | Numba 秒 | 加速 |
|---|---|---:|---:|---:|
| fp32.exp | batch | 4.612 | 0.036 | 126.36× |
| fp32.exp | cycle-unit | 12.603 | 2.271 | 5.55× |
| fp32.exp | spm-network | 47.321 | 2.560 | 18.49× |
| fp32.rcp | batch | 3.905 | 0.034 | 115.13× |
| fp32.rcp | cycle-unit | 11.114 | 2.245 | 4.95× |
| fp32.sqrt | batch | 3.731 | 0.046 | 80.61× |
| fp32.sqrt | cycle-unit | 11.634 | 2.273 | 5.12× |
| fp32.rsqrt | batch | 3.715 | 0.098 | 37.93× |
| fp32.rsqrt | cycle-unit | 13.177 | 2.337 | 5.64× |
| fp32.rsqrt | spm-network | 63.547 | 2.794 | 22.74× |
| fp16.exp | batch | 4.746 | 0.035 | 134.63× |
| fp16.exp | cycle-unit | 11.368 | 2.221 | 5.12× |
| fp16.exp | spm-network | 45.642 | 2.494 | 18.30× |
| fp16.rcp | batch | 4.023 | 0.031 | 130.05× |
| fp16.rcp | cycle-unit | 9.529 | 2.211 | 4.31× |
| fp16.sqrt | batch | 3.566 | 0.043 | 83.17× |
| fp16.sqrt | cycle-unit | 9.987 | 2.241 | 4.46× |
| fp16.rsqrt | batch | 3.629 | 0.059 | 61.27× |
| fp16.rsqrt | cycle-unit | 10.105 | 2.250 | 4.49× |
| fp16.rsqrt | spm-network | 59.531 | 2.605 | 22.85× |
| bf16.exp | batch | 4.334 | 0.031 | 139.73× |
| bf16.exp | cycle-unit | 11.341 | 2.249 | 5.04× |
| bf16.exp | spm-network | 45.656 | 2.508 | 18.20× |
| bf16.rcp | batch | 3.696 | 0.029 | 128.09× |
| bf16.rcp | cycle-unit | 9.519 | 2.215 | 4.30× |
| bf16.sqrt | batch | 3.495 | 0.043 | 81.10× |
| bf16.sqrt | cycle-unit | 9.843 | 2.248 | 4.38× |
| bf16.rsqrt | batch | 3.505 | 0.050 | 70.66× |
| bf16.rsqrt | cycle-unit | 9.854 | 2.246 | 4.39× |
| bf16.rsqrt | spm-network | 59.037 | 2.617 | 22.56× |

输入和周期激励预生成，五轮交替测量 Python 与 Numba 并取中位数。编译单独计时；测量前比较完整输出、周期轨迹与最终状态。RSS 为进程峰值，逐项编译时间、运行时间和主机配置保存在 [机器可读报告](sfu-validation.json)。

组合网络覆盖 SPM → exp → SPM、SPM → rsqrt → mul → SPM，包含背压、复位、清空、最终存储内容和周期边界后端切换。
