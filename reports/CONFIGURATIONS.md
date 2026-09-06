# 实测配置清单

共同契约 SHA-256：`5665fcf966d1484782a42f648864ee18044bb5620b63da9ea196492f092c4320`。

典型角 TC，ASAP7 RVT，0.70 V，0 °C，1 GHz；IO 预算 200 ps，时钟不确定度 50 ps。

`dual-verified` 表示数值、逐周期与上述物理条件均通过。全局布线使用估算 RC；详细布线结果明确单列，均不代表流片签核或形式等价验证。

| 配置 | L / II | 数值 | 周期 | 面积 µm² | setup / hold ps | 物理级别 | 状态 |
|---|---:|---|---|---:|---:|---|---|
| fp32.add | 6 / 1 | passed | passed | 689.065 | 26.68 / 3.01 | global-route-estimated-RC | dual-verified |
| fp32.mul | 7 / 1 | passed | passed | 1097.03 | 5.47 / 2.20 | global-route-estimated-RC | dual-verified |
| fp32.fma | 8 / 1 | passed | passed | 1615.2 | 13.21 / 1.20 | detailed-route | dual-verified |
| fp16.add | 6 / 1 | passed | passed | 443.684 | 94.39 / 2.55 | global-route-estimated-RC | dual-verified |
| fp16.mul | 7 / 1 | passed | passed | 477.276 | 156.56 / 2.37 | global-route-estimated-RC | dual-verified |
| fp16.fma | 8 / 1 | passed | passed | 779.345 | 17.16 / 2.35 | global-route-estimated-RC | dual-verified |
| e4m3fn.add | 4 / 1 | passed | passed | 240.964 | 23.79 / 2.74 | global-route-estimated-RC | dual-verified |
| e4m3fn.mul | 4 / 1 | passed | passed | 187.018 | 159.61 / 2.78 | global-route-estimated-RC | dual-verified |
| e4m3fn.fma | 5 / 1 | passed | passed | 360.053 | 2.37 / 2.75 | global-route-estimated-RC | dual-verified |
| e5m2.add | 4 / 1 | passed | passed | 245.279 | 3.57 / 2.43 | global-route-estimated-RC | dual-verified |
| e5m2.mul | 4 / 1 | passed | passed | 185.924 | 261.89 / 2.74 | global-route-estimated-RC | dual-verified |
| e5m2.fma | 5 / 1 | passed | passed | 347.164 | 21.10 / 9.72 | global-route-estimated-RC | dual-verified |
| e2m1.add | 1 / 1 | passed | passed | 46.0582 | 202.44 / 15.21 | global-route-estimated-RC | dual-verified |
| e2m1.mul | 1 / 1 | passed | passed | 44.2211 | 117.34 / 3.59 | global-route-estimated-RC | dual-verified |
| e2m1.fma | 2 / 1 | passed | passed | 88.8214 | 254.94 / 3.29 | global-route-estimated-RC | dual-verified |
| fp32.div | 19 / 19 | passed | passed | 754.894 | 38.46 / 6.81 | detailed-route | dual-verified |
| fp16.div | 13 / 13 | passed | passed | 450.26 | 107.86 / 2.87 | global-route-estimated-RC | dual-verified |
| e4m3fn.div | 12 / 12 | passed | passed | 251.738 | 194.33 / 2.42 | global-route-estimated-RC | dual-verified |
| e5m2.div | 11 / 11 | passed | passed | 250.63 | 224.02 / 2.11 | global-route-estimated-RC | dual-verified |
| e2m1.div | 1 / 1 | passed | passed | 49.4845 | 401.57 / 4.36 | global-route-estimated-RC | dual-verified |
| int8.add | 1 / 1 | passed | passed | 37.004 | 470.73 / 10.49 | global-route-estimated-RC | dual-verified |
| int8.add.unsigned | 1 / 1 | passed | passed | 37.4414 | 472.94 / 2.77 | global-route-estimated-RC | dual-verified |
| int8.mul | 2 / 1 | passed | passed | 97.3944 | 450.29 / 5.83 | global-route-estimated-RC | dual-verified |
| int8.mul.unsigned | 2 / 1 | passed | passed | 93.487 | 443.67 / 3.54 | global-route-estimated-RC | dual-verified |
| int8.div | 10 / 10 | passed | passed | 123.303 | 264.05 / 2.79 | global-route-estimated-RC | dual-verified |
| int8.div.unsigned | 10 / 10 | passed | passed | 111.012 | 310.95 / 1.07 | global-route-estimated-RC | dual-verified |
| int16.add | 1 / 1 | passed | passed | 49.018 | 461.23 / 11.77 | global-route-estimated-RC | dual-verified |
| int16.add.unsigned | 1 / 1 | passed | passed | 48.7993 | 457.00 / 11.39 | global-route-estimated-RC | dual-verified |
| int16.mul | 2 / 1 | passed | passed | 254.713 | 98.10 / 6.37 | global-route-estimated-RC | dual-verified |
| int16.mul.unsigned | 2 / 1 | passed | passed | 258.562 | 106.92 / 7.95 | global-route-estimated-RC | dual-verified |
| int16.div | 13 / 13 | passed | passed | 400.79 | 54.97 / 1.64 | global-route-estimated-RC | dual-verified |
| int16.div.unsigned | 13 / 13 | passed | passed | 367.883 | 95.32 / 3.01 | global-route-estimated-RC | dual-verified |
| int32.add | 1 / 1 | passed | passed | 69.9403 | 388.24 / 6.63 | global-route-estimated-RC | dual-verified |
| int32.add.unsigned | 1 / 1 | passed | passed | 71.0775 | 359.87 / 5.29 | global-route-estimated-RC | dual-verified |
| int32.mul | 3 / 1 | passed | passed | 904.281 | 67.35 / 4.77 | global-route-estimated-RC | dual-verified |
| int32.mul.unsigned | 3 / 1 | passed | passed | 921.85 | 47.09 / 4.25 | global-route-estimated-RC | dual-verified |
| int32.div | 21 / 21 | passed | passed | 766.296 | 57.38 / 3.27 | global-route-estimated-RC | dual-verified |
| int32.div.unsigned | 21 / 21 | passed | passed | 706.372 | 64.05 / 2.95 | global-route-estimated-RC | dual-verified |

历史候选和失败测量保留于 `validation.json` 的 `physical` 字段。候选仅在正确性、时序和吞吐硬约束通过后比较面积×延迟；差异不足 5% 时优先较小面积。当前未通过配置保持未合格，不把初始结构声明为全局最优。
