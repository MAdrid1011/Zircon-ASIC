# 文档导航

Zircon-ASIC 的文档分为共同规范、Python 模拟器、Chisel 硬件和验证记录四层。阅读或修改某一层时，应同时确认其与共同契约的关系。

| 入口 | 适用场景 |
|---|---|
| [共同契约](contract.md) | 查询格式、舍入、异常、延迟、握手与清空规则 |
| [Python 模拟器结构](implementation.md) | 用模块搭建周期模型，或修改批量/网络执行 |
| [硬件架构总览](hardware/README.md) | 查找 Chisel 源文件对应的模块文档 |
| [验证与复现](verification.md) | 运行数值、逐周期、RTL 与物理评估流程 |
| [配置清单](../reports/CONFIGURATIONS.md) | 查看当前配置的验证、时延和物理状态 |
| [SPM 验证记录](../reports/SPM.md) | 查看真实 SRAM 宏、三方对齐、CPU 性能与 IHP 物理结果 |
| [结构取舍](../reports/DECISIONS.md) | 查看已测候选之间的实现选择 |

硬件模块文档按源码文件组织：

- [接口与流水控制](hardware/interface.md)
- [整数算术](hardware/integer.md)
- [浮点加乘与 FMA](hardware/floating.md)
- [浮点除法与 FP4](hardware/division-and-fp4.md)
- [指数函数 exp](hardware/exp.md)
- [倒数 rcp](hardware/rcp.md)
- [平方根 sqrt](hardware/sqrt.md)
- [倒平方根 rsqrt](hardware/rsqrt.md)
- [传输与组合](hardware/transport.md)
- [普通 SPM](hardware/spm.md)
- [集成与生成](hardware/integration.md)

改变公共格式、端口、阶段、容量、迭代次数或实现变体时，先修改共同契约，再同步 Python、Chisel、验证和相关模块页面。不能只修改其中一端而沿用旧验证状态。
