# Zircon-ASIC

Zircon-ASIC 提供独立安装的 Python 位精确计算、周期模拟器和原生 Chisel 算术部件。两端读取同一份版本化契约，验证数值、请求接收、输出有效、背压、标签、级占用、除法迭代、复位与清空。

当前为开发版。数值正确、周期对齐、ASAP7 时序合格是三项独立门槛；没有完成全部门槛的配置不标记为正式双实现。实测记录由 `scripts/collect_reports.py` 汇总，硬件级数以包内契约为准。初始 FP32 流水级数在物理评估后已调整，仍需完成时序收敛。

## 安装与计算

```sh
python -m pip install .          # Python 和 NumPy 即可
python -m pip install '.[fast]'   # 可选 Numba 加速
```

```python
from zircon_asic import FP32Fma, INT8Div, Request, Rounding

fma = FP32Fma()
result = fma.compute(Request(0x3f800000, 0x40000000, 0x40400000))
assert result.bits == 0x40a00000  # 1 * 2 + 3 = 5

division = INT8Div()
result = division.compute(Request(0xf9, 3))  # -7 / 3
assert result.bits == 0xfe and result.remainder == 0xff

batch = fma.compute_batch([0x3f800000] * 1024, 0x40000000, 0x40400000)
print(fma.describe())
```

输入、输出使用原始位编码，避免宿主浮点类型改变 NaN、舍入或低精度格式。`compute_batch` 支持 NumPy 广播，返回 `bits`、`flags`、`tags` 和 `remainder` 数组；`backend="python"` 和 `backend="numba"` 可显式选择实现。`auto` 在没有安装 Numba 时回退到完整 Python 实现。

| 格式 | 类前缀 | 操作 |
|---|---|---|
| binary32 / binary16 | `FP32` / `FP16` | `Add`、`Mul`、`Fma`、`Div` |
| E4M3FN / E5M2 | `FP8E4M3FN` / `FP8E5M2` | 同上 |
| E2M1 | `FP4` | 同上 |
| INT8 / INT16 / INT32 | `INT8` / `INT16` / `INT32` | `Add`、`Mul`、`Div` |

参数化类为 `FpAdd/FpMul/FpFma/FpDiv(format)` 和 `IntAdd/IntMul/IntDiv(width, signed=True)`。整数无符号模式使用 `signed=False`。所有类具有相同的计算与周期方法。

## 周期模拟

```python
from zircon_asic import FP16Add, Inputs, Request

unit = FP16Add()
for cycle in range(8):
    request = Request(0x3c00, 0x4000, tag=7) if cycle == 0 else None
    ports = unit.step(Inputs(request, out_ready=cycle != 2))
    if ports.delivered:
        print(cycle, ports.response)
```

`eval(inputs)` 观察本拍端口，`tick()` 提交状态；`step(inputs)` 完成两者。接收发生在第 `k` 拍、无背压延迟为 `L` 时，输出在 `k+L` 拍交付。输出被阻塞时保持内容和 `valid`。`Inputs(reset=True)` 和 `Inputs(flush=True)` 优先于握手，取消全部在途请求。

`reset()` 是带统计清零的立即复位；`flush()` 立即取消在途请求并保留累计统计。需要与 RTL 比较时使用随拍输入。功能计算不改变周期状态。

```python
from zircon_asic import Network, FIFO, DelayLine, INT8Add, INT8Mul, Request

net = Network().add("add", INT8Add()).add("fifo", FIFO(3)).add("mul", INT8Mul())
net.connect("add", "fifo").connect("fifo", "mul", b=3)
net.source("add", [Request(i, 2, tag=i) for i in range(32)]).sink("mul")
results = net.run(100, backend="numba")
```

网络统一观察后提交。当前网络支持单输入、单输出节点组成的有向链和多条独立链，显式拒绝多驱动、隐式广播及环；连接可指定第二、第三操作数。FIFO、延迟线和整个内置网络都支持 Numba 循环。任意 Python 回调、动态路由、广播和多输入汇合不在当前编译调度器的支持范围内。

## Chisel 与验证

需要 JDK 21、sbt、C++ 编译器和 Verilator。Python 基础安装不需要这些工具。

```sh
cd hardware
sbt 'runMain zircon.Generate fp32 fma ../build/rtl/fp32_fma'
cd ..
python scripts/build_oracles.py
python -m pytest -q
python scripts/validate_rtl.py
python scripts/validate_network_rtl.py
python scripts/exhaustive.py
python scripts/exhaustive_rtl.py
```

Chisel 输出 `Unit.sv` 和带共同契约的 `manifest.json`。随机验证逐拍比较端口、有效输出全部字段及内部观察点，包含长背压、气泡、复位和清空。失败保留刺激、随机种子、首个分歧周期和双边轨迹。

`exhaustive.py` 使用独立 C++ 128 位精确有理数参考，覆盖 FP4/FP8 全部二元和 FMA 输入、五种舍入方式。`exhaustive_rtl.py` 对同一参考验证 RTL 数值和无背压逐笔延迟。FMA 可使用 `--shard 0 --shards 256` 分片执行。FP32/FP16 使用固定提交的 Berkeley SoftFloat 测试参考；生产路径不链接 SoftFloat。

物理评估使用固定摘要的 OpenROAD 容器和 ASAP7 RVT 库，命令为 `python scripts/ppa.py fp32.fma`。默认最差角 SS、0.63 V、100 °C，1000 ps 周期、200 ps 输入输出预算、50 ps 时钟不确定度。报告会保留失败和负裕量；流程结束不等于时序通过。

详细说明见 [数值与接口契约](docs/contract.md)、[模拟器与硬件结构](docs/implementation.md) 和 [验证与复现](docs/verification.md)。脉动阵列、FFT、混合精度累加和块缩放格式属于后续扩展。
