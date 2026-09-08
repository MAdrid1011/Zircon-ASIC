# 集成、共同契约与 RTL 生成

这一章描述 `Arithmetic.scala`、`Contract.scala` 和 `Generate.scala`。它们负责在单独的 Chisel 工程中选择模块、加载共同规范并生成内容可追溯的 SystemVerilog。

## Arithmetic 工厂

`Arithmetic(name, op, signed, contract)` 是调用方的单点入口。它读取 `contract.spec(name, op)` 后选择具体 Chisel 类：

| 输入类别 | `add` | `mul` | `fma` | `div` |
|---|---|---|---|---|
| `fp32`、`fp16`、`bf16`、`e4m3fn`、`e5m2` | `FpAdd` | `FpMul` | `FpFma` | `FpDiv` |
| `e2m1` | `Fp4Arithmetic` | `Fp4Arithmetic` | `Fp4Arithmetic` | `Fp4Div` |
| `int8`、`int16`、`int32` | `IntAdd` | `IntMul` | 不适用 | `IntDiv` |

`fp32`、`fp16`、`bf16` 还支持 `exp`、`rcp`、`rsqrt`、`sqrt`，分别生成 `FpExp`、`FpRcp`、`FpRsqrt`、`FpSqrt`。这些一元模块保留同一请求 Bundle，只使用 `a`，未使用的 `b/c` 应由集成方连接到确定值。

整数模块把 `signed` 传给构造器；浮点模块忽略该参数。工厂返回 `ArithmeticModule`，所以调用方可统一访问 `io.in`、`io.out`、`io.flush` 和观察端口。

## Contract

`Contract` 读取 JSON 后要求 `schema_version=1` 和 `interface_version=1`。`format(name)` 返回 `Format`：编码位宽、指数位数、尾数位数、偏置及编码类别；同时计算有效数精度 `p`、内部指数宽度 `ew`、最小/最大指数、最大有限编码、无穷编码和规范 NaN 编码。

`spec(name, op)` 返回 `Spec`：模块名、操作、延迟、控制类型、阶段序列和实现变体。`iterations` 由阶段中 `iterate` 的数量计算，避免在 RTL 与 Python 中维护两份独立迭代次数。

默认的 `Contract.bundled()` 从 Chisel JAR 中读取 `/zircon-contract.json`。构建脚本把 Python 包内的 `src/zircon_asic/data/contract.json` 复制为该资源；因此正常发布的 Python 与 Chisel 包携带同一规范。候选配置可用 `new Contract(path)` 或 `ZIRCON_CONTRACT` 传入，但其验证状态必须重新建立。

非线性算子的表、系数、阈值和逐级定点参数打包为 `/zircon-sfu.json`，与 Python 包中的 `data/sfu.json` 字节相同。每项契约通过 SHA-256 绑定该资源，硬件从对应实现配置读取参数。

## 生成 RTL

`Generate` 的调用格式为：

```bash
cd hardware
sbt 'runMain zircon.Generate FORMAT OP OUTPUT_DIR [signed|unsigned]'
```

它创建 `Arithmetic` 顶层，调用 `ChiselStage.emitSystemVerilog`，并写出：

- `Unit.sv`：独立的 SystemVerilog 顶层。
- `manifest.json`：格式、操作、符号模式、延迟、控制类型、阶段、变体、完整共同契约和 `Unit.sv` 的 SHA-256。

`GenerateAll` 从共同契约遍历全部浮点与整数操作，并为每个整数操作额外生成无符号顶层。它适合 RTL 对齐回归；实际集成通常只生成或实例化所需模块。

## 作为依赖使用

在 `hardware` 目录执行：

```bash
sbt publishLocal
```

调用项目可引用：

```scala
libraryDependencies += "org.zirconasic" %% "zircon-asic" % "0.3.0"
```

库使用 Scala 2.13.18、Chisel 7.15.0。`build.sbt` 已将共同契约打包为资源；使用方不需要安装 Python。生成的各个独立 `Unit.sv` 可能拥有相同的顶层模块名，因此不要直接把多个文件拼接到同一编译单元。对于多模块设计，应在自己的 Chisel 顶层内实例化 `Arithmetic`，再一次性生成 RTL。

## 变更规则

格式、延迟、阶段、容量、除法迭代规则或接口版本的改变，都会改变共同契约或 RTL SHA-256。此时必须同步更新 Python 周期模型、重新生成 RTL、重新运行逐周期检查，并更新相应硬件文档与报告。详情见 [验证与复现](../verification.md)。
