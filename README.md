# Zircon-ASIC

> 位精确算术、片上存储与逐周期对齐的 Chisel IP 库。

Zircon-ASIC 提供可独立安装的 Python 模拟器和生成 SystemVerilog 的 Chisel 硬件库，用于构建加速器与自定义计算架构。两端读取同一份版本化契约，在相同请求、背压、复位和清空序列下逐拍核对数值结果、握手与流水状态。

| 浮点格式 | 操作 | 整数格式 | 操作 |
|---|---|---|---|
| FP32、FP16、BF16、FP8 E4M3FN/E5M2、FP4 E2M1 | add · mul · FMA · div | INT8、INT16、INT32 | add · mul · div（有符号/无符号） |

**非线性算子** 提供 FP32、FP16、BF16 的 exp、rcp、rsqrt、sqrt 共 12 个独立单元。exp 采用 RNE 打包、误差严格小于 1 ULP；其余三个算子支持五种模式的正确舍入。所有单元均支持次正规数，采用每拍可接收一笔请求的弹性流水。

**SPM** 支持 8/16/32/64 bit 数据、1/2/4/8/16 个 bank、1～8 个请求端、字节写掩码与轮询仲裁。无背压访问延迟为 2 拍，每 bank 的启动间隔为 1 拍。算术、SPM、FIFO 和延迟线可以通过 `Network` 组合，使用 Python 或 Numba 执行同一架构模型。

## 核心能力

- **位精确数值路径**：原始位编码、五种舍入方式、异常标志、次正规数、NaN/Inf 与有限 FP8/FP4 的明确规则。
- **周期模型可用于架构探索**：统一 `valid/ready`、流水占用、迭代阶段、背压、reset 与 flush；`tag` 可关联每笔事务的接收和交付周期。
- **硬件实现可直接集成**：原生 Chisel `Decoupled` 接口，按宽度选择加法器、部分积压缩树和迭代除法器。
- **独立非线性计算**：范围缩减与定点多项式、完整有效数表、空间展开的迭代及精确余量校正；同一编码在功能、批量和周期路径中返回一致结果。
- **共享存储与模块连接**：显式选择请求端，映射响应字段和常量，统一提交各模块状态。

## 快速开始：Python

基础安装只需要 Python 3.11+ 和 NumPy；Numba 是批量计算与内置网络的可选加速后端。

```bash
git clone https://github.com/MAdrid1011/Zircon-ASIC.git
cd Zircon-ASIC
python -m pip install .
# 可选：安装 Numba 加速后端
python -m pip install '.[fast]'
```

功能计算不修改周期状态：

```python
from zircon_asic import FP32Fma, Request

fma = FP32Fma()
response = fma.compute(Request(0x3F800000, 0x40000000, 0x40400000))
assert response.bits == 0x40A00000  # 1.0 × 2.0 + 3.0 = 5.0
```

一元算子只需提供 `a`，批量接口接受原始位编码数组：

```python
from zircon_asic import FP32Exp, BF16Rsqrt, Request

exp = FP32Exp()
assert exp.compute(Request(a=0)).bits == 0x3F800000  # exp(0) = 1
assert BF16Rsqrt().compute(Request(a=0x4080)).bits == 0x3F00  # 1/sqrt(4) = 0.5
values = exp.compute_batch([0, 0x3F800000], backend="python")
latency = exp.describe()["latency"]
```

安装 Numba 后可使用 `backend="numba"`。完整存储组合见 [SPM → exp → SPM 与 rsqrt → 乘法示例](examples/nonlinear_spm.py)。

逐周期模拟使用 `step()`；调用前读取 `unit.cycle`，即可记录该拍的握手事件：

```python
from zircon_asic import FP16Add, Inputs, Request

unit = FP16Add()
for _ in range(16):
    cycle = unit.cycle
    ports = unit.step(Inputs(
        Request(0x3C00, 0x4000, tag=7) if cycle == 0 else None,
        out_ready=(cycle != 6),
    ))
    if ports.delivered:
        print(f"delivered at cycle {cycle}: {ports.response}")
```

`accepted` 表示请求在本拍被接收，`out_valid` 表示结果已到输出端，`delivered` 表示结果与下游完成握手。无背压时，在第 `k` 拍接收、延迟为 `L` 的请求会在第 `k+L` 拍交付。完整语义见 [共同契约](docs/contract.md)。

SPM 使用相同的周期接口，读写请求通过 `MemoryRequest` 表达：

```python
from zircon_asic import SPM, MemoryRequest, Inputs

spm = SPM(capacity_bytes=4096, data_width=32, banks=1, ports=1)
spm.load_image(bytes(4096))
spm.compute(MemoryRequest(address=0x100, write=True, data=17))
assert spm.step(Inputs(MemoryRequest(address=0x100, tag=7))).accepted
spm.step()
out = spm.step()
assert out.delivered and out.response.bits == 17  # 第 0 拍接收，第 2 拍交付
```

参见 [SPM 文档](docs/hardware/spm.md)、[读 SPM → 乘法 → 写 SPM 示例](examples/spm.py) 和 [用户控制器示例](examples/spm_controller.py)。

## 快速开始：Chisel

Chisel 库需要 JDK 21 与 sbt。在本仓库的 `hardware` 目录执行 `sbt publishLocal`，然后在调用工程的 `build.sbt` 中添加：

```scala
scalaVersion := "2.13.18"
libraryDependencies += "org.zirconasic" %% "zircon-asic" % "0.3.0"
addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % "7.15.0" cross CrossVersion.full)
```

`Arithmetic` 根据内置共同契约实例化算术单元。以下顶层完整连接请求、响应与清空接口：

```scala
import chisel3._
import chisel3.util._
import zircon._

class MyDatapath extends Module {
  val io = IO(new Bundle {
    val in = Flipped(Decoupled(new zircon.Request(32)))
    val out = Decoupled(new zircon.Response(32))
    val flush = Input(Bool())
  })
  val fma = Module(Arithmetic("fp32", "fma"))
  fma.io.in <> io.in
  io.out <> fma.io.out
  fma.io.flush := io.flush
}
```

从源码生成一个独立 RTL 顶层：

```bash
cd hardware
sbt 'runMain zircon.Generate fp32 fma ../build/rtl/fp32_fma'
```

输出目录含 `Unit.sv` 与 `manifest.json`。后者绑定格式、时延、阶段、共同契约和 RTL SHA-256。接口、阻塞和接线要求见 [硬件接口与控制](docs/hardware/interface.md)。

## 模块与时序

| 模块族 | 操作 | 控制类型 | 延迟（拍） |
|---|---|---|---|
| FP32 | add / mul / FMA / div | 弹性流水 / 迭代 | 6 / 7 / 8 / 19 |
| FP16 | add / mul / FMA / div | 弹性流水 / 迭代 | 6 / 7 / 8 / 13 |
| BF16 | add / mul / FMA / div | 弹性流水 / 迭代 | 6 / 4 / 8 / 16 |
| E4M3FN | add / mul / FMA / div | 弹性流水 / 迭代 | 4 / 4 / 5 / 12 |
| E5M2 | add / mul / FMA / div | 弹性流水 / 迭代 | 4 / 4 / 5 / 11 |
| E2M1 | add / mul / FMA / div | 弹性流水 | 1 / 1 / 2 / 1 |
| INT8 | add / mul / div | 弹性流水 / 迭代 | 1 / 2 / 10 |
| INT16 | add / mul / div | 弹性流水 / 迭代 | 1 / 2 / 13 |
| INT32 | add / mul / div | 弹性流水 / 迭代 | 1 / 3 / 21 |
| FP32 一元函数 | exp / rcp / sqrt / rsqrt | 弹性流水 | 17 / 20 / 20 / 34 |
| FP16 一元函数 | exp / rcp / sqrt / rsqrt | 弹性流水 | 10 / 5 / 5 / 5 |
| BF16 一元函数 | exp / rcp / sqrt / rsqrt | 弹性流水 | 10 / 5 / 5 / 5 |
| SPM | read / write | 同步存储与响应队列 | 2 |

弹性算术单元（含全部一元函数）的启动间隔为 1；迭代除法器的无背压启动间隔等于完整延迟。SPM 每 bank 每拍服务一次读或写。阶段、容量和实现变体分别由 [算术契约](src/zircon_asic/data/contract.json) 与 [SPM 契约](src/zircon_asic/data/spm.json) 定义。

## 验证状态

当前 54 个算术配置均完成数值与 Python／RTL 逐周期回归。逐周期检查比较 `in_ready`、`out_valid`、有效响应字段与内部观察点，周期差异为 0。各配置的面积、时序裕量和物理阶段见 [配置清单](reports/CONFIGURATIONS.md)、[结构取舍](reports/DECISIONS.md) 和 [验证文档](docs/verification.md)。

FP32、FP16、BF16 的 12 个一元配置均完成独立数值参考、Python／Numba／RTL 三方逐周期回归、SPM 组合回归与 ASAP7 TC 1 GHz 的详细布线评估。数值回归覆盖 FP16、BF16 全输入空间与 FP32 的分层百万输入集；FP32 rcp、sqrt、rsqrt 另外穷举规格化有效数通路。完整的数值、时序、面积和 CPU 记录见 [非线性算子验证记录](reports/SFU.md)。

SPM 的 4 KiB / 1 bank / 1 请求端和 16 KiB / 4 banks / 4 请求端两个 32 bit 配置，已完成 IHP SG13G2 真实 SRAM 宏的详细布线、寄生提取和 **100 MHz** 两角时序检查，以及综合网表功能回放。Python、Numba、RTL 各配置运行 5 组十万拍长回归，周期差异为 0。完整结果、CPU 加速测量与配置限制见 [SPM 验证记录](reports/SPM.md)。这些结果是公开 PDK 下的实现证据。

| 硬件 | 工艺与测试角 | 时钟 | 物理阶段 |
|---|---|---:|---|
| BF16 add / mul / FMA / div | ASAP7 RVT，TC 0.70 V / 0 °C | 1 GHz | 详细布线、提取 RC、STA 与路由审计 |
| FP32 / FP16 / BF16 exp / rcp / sqrt / rsqrt | ASAP7 RVT，TC 0.70 V / 0 °C | 1 GHz | 详细布线、提取 RC、STA、路由审计与网表回放 |
| 既有算术配置 | ASAP7 RVT，TC 0.70 V / 0 °C | 1 GHz | FP32 FMA／除法为详细布线，其余为全局布线与估算 RC |
| 两项 SPM 参考配置 | IHP SG13G2，TT 1.20 V / 25 °C、SS 1.08 V / 125 °C | 100 MHz | 真实 SRAM 宏、详细布线、提取 RC、两角 STA |

## 文档导航

从 [文档导航](docs/README.md) 或 [硬件架构总览](docs/hardware/README.md) 开始。每个 Chisel 源文件都有对应的模块说明。

| 文档 | 内容 |
|---|---|
| [共同契约](docs/contract.md) | 位编码、舍入、异常、周期定义 |
| [接口与流水控制](docs/hardware/interface.md) | `Request`、`Response`、`ElasticModule`、`IterativeModule` |
| [整数算术](docs/hardware/integer.md) | 加法器、Dadda 压缩树、整数除法 |
| [浮点加乘与 FMA](docs/hardware/floating.md) | 解码、对齐、规格化、舍入、特殊值 |
| [浮点除法与 FP4](docs/hardware/division-and-fp4.md) | SRT/非恢复除法与 E2M1 精确路径 |
| [exp](docs/hardware/exp.md)、[rcp](docs/hardware/rcp.md)、[rsqrt](docs/hardware/rsqrt.md)、[sqrt](docs/hardware/sqrt.md) | 一元接口、定点通路、精度与特殊值 |
| [传输与组合](docs/hardware/transport.md) | FIFO、延迟线、链式网络和边界 |
| [普通 SPM](docs/hardware/spm.md) | 寻址、写掩码、轮询仲裁、周期接口与 SRAM 宏 |
| [集成与生成](docs/hardware/integration.md) | 工厂、契约加载、JAR、RTL 生成 |
| [验证与复现](docs/verification.md) | 数值、周期、RTL 与物理评估 |

## 目录

```text
src/zircon_asic/                 Python 数值、周期与网络模拟器
hardware/src/main/scala/zircon/  Chisel 算术、存储、传输与生成器
docs/hardware/                   按硬件模块组织的结构说明
tests/                           Python 数值与周期测试
scripts/                         RTL 对齐、外部参考、报告和 PPA 脚本
reports/                         已生成的配置、取舍和性能记录
examples/                        独立调用与网络组合示例
```

## 许可证与贡献

Zircon-ASIC 使用 [Apache-2.0](LICENSE) 许可证。第三方组件见 [许可与来源](THIRD_PARTY_NOTICES.md)。问题反馈与改进提交见 [贡献指南](CONTRIBUTING.md)，安全问题见 [报告渠道](SECURITY.md)。
