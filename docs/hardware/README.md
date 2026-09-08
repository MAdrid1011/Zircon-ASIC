# 硬件架构总览

这一章描述 Zircon-ASIC Chisel 库的模块边界。顶层工厂 `Arithmetic` 根据共同契约实例化单个算术单元；每个单元使用统一 `UnitIO`，由 `ArithmeticModule` 提供清空、输出稳定性断言和调试观察端口。整数与浮点数据通路各自实现，传输模块用于构造上层流式架构。`SPM` 通过独立访存请求／响应接口提供共享存储，每个 bank 对多个请求端轮询仲裁。

调用者通过 `io.in` 发送 `Request`，通过 `io.out` 接收 `Response`。弹性算术单元由 `ElasticModule` 管理逐级有效位；迭代除法器由 `IterativeModule` 管理单笔请求的阶段计数。两类模块均接收 `io.flush`，并在复位或清空的周期抑制输入和输出握手。共同契约指定格式、操作、阶段名称、延迟和实现变体；Python 周期模型读取同一份数据。

| 源文件 | 主要定义 | 说明页 |
|---|---|---|
| `Arithmetic.scala` | `Arithmetic` | [集成与生成](integration.md) |
| `Contract.scala` | `Format`、`Spec`、`Contract` | [集成与生成](integration.md) |
| `Interface.scala` | `Request`、`Response`、`UnitIO`、控制基类 | [接口与流水控制](interface.md) |
| `Integer.scala` | `IntAdd`、`IntMul`、`IntDiv`、加法/压缩树工具 | [整数算术](integer.md) |
| `Floating.scala` | `FpAdd`、`FpMul`、`FpFma`、浮点公共逻辑 | [浮点加乘与 FMA](floating.md) |
| `FloatDiv.scala` | `FpDiv`、`Fp4Div` | [浮点除法与 FP4](division-and-fp4.md) |
| `SmallFloating.scala` | `Fp4Arithmetic` | [浮点除法与 FP4](division-and-fp4.md) |
| `Unary.scala` | `FpExp`、`FpRcp`、`FpSqrt`、`FpRsqrt`、定点乘法与资源加载 | [exp](exp.md)、[rcp](rcp.md)、[sqrt](sqrt.md)、[rsqrt](rsqrt.md) |
| `Transport.scala` | `DelayLine`、`StreamFIFO`、`NetworkExample` | [传输与组合](transport.md) |
| `Generate.scala` | `Generate`、`GenerateAll` | [集成与生成](integration.md) |
| `SPM.scala` | `SPMConfig`、`SPM`、`SRAMBank`、IHP 宏适配器 | [普通 SPM](spm.md) |
| `SPMExample.scala` | `SPMExample`、`GenerateSPMExample` | [普通 SPM](spm.md) |
| `UnarySPMExample.scala` | `UnarySPMExample`、`GenerateUnarySPMExample` | [非线性与 SPM 组合](../../examples/nonlinear_spm.py) |

格式编码、异常标志和周期定义属于 [共同契约](../contract.md)；Python 仿真器的对象模型与 Numba 执行方式属于 [模拟器与 Chisel 结构](../implementation.md)。
