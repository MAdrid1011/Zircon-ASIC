# 实测配置清单

共同契约 SHA-256：`9208c9d6f1f231938419555263f2c0417528b0fa3d7e65db46976f49dc985ffa`。

典型角 TC，ASAP7 RVT，0.70 V，0 °C，1 GHz；IO 预算 200 ps，时钟不确定度 50 ps。

`dual-verified` 表示数值、逐周期与上述物理条件均通过。物理级别列区分全局布线估算 RC 与详细布线提取 RC。

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
| bf16.add | 6 / 1 | passed | passed | 409.625 | 191.33 / 1.29 | detailed-route | dual-verified |
| bf16.mul | 4 / 1 | passed | passed | 292.912 | 78.83 / 1.01 | detailed-route | dual-verified |
| bf16.fma | 8 / 1 | passed | passed | 673.596 | 67.73 / 0.73 | detailed-route | dual-verified |
| bf16.div | 16 / 16 | passed | passed | 350.532 | 154.03 / 1.17 | detailed-route | dual-verified |
| fp32.exp | 17 / 1 | passed | passed | 2898.8 | 70.83 / 5.19 | detailed-route | dual-verified |
| fp32.rcp | 20 / 1 | passed | passed | 2140.99 | 42.97 / 1.01 | detailed-route | dual-verified |
| fp32.sqrt | 20 / 1 | passed | passed | 2165.23 | 54.54 / 0.78 | detailed-route | dual-verified |
| fp32.rsqrt | 34 / 1 | passed | passed | 8263.71 | 38.37 / 0.71 | detailed-route | dual-verified |
| fp16.exp | 10 / 1 | passed | passed | 996.178 | 34.12 / 1.18 | detailed-route | dual-verified |
| fp16.rcp | 5 / 1 | passed | passed | 411.666 | 276.80 / 1.28 | detailed-route | dual-verified |
| fp16.sqrt | 5 / 1 | passed | passed | 530.304 | 237.14 / 0.68 | detailed-route | dual-verified |
| fp16.rsqrt | 5 / 1 | passed | passed | 512.691 | 168.59 / 1.02 | detailed-route | dual-verified |
| bf16.exp | 10 / 1 | passed | passed | 911.512 | 37.51 / 0.93 | detailed-route | dual-verified |
| bf16.rcp | 5 / 1 | passed | passed | 290.404 | 183.09 / 1.12 | detailed-route | dual-verified |
| bf16.sqrt | 5 / 1 | passed | passed | 300.581 | 258.09 / 0.67 | detailed-route | dual-verified |
| bf16.rsqrt | 5 / 1 | passed | passed | 309.898 | 181.96 / 1.35 | detailed-route | dual-verified |

候选测量保存在 `validation.json` 的 `physical` 字段。结构选择在正确性、时序和吞吐约束下比较面积×延迟；差异不足 5% 时优先较小面积。
