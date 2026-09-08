# 第三方组件与来源

Zircon-ASIC 源码采用 [Apache-2.0](LICENSE)。第三方文件保留各自的版权、许可证和来源记录。

## SRAM 与标准单元视图

SPM 物理证据包包含 [IHP Open PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) 的 SRAM 模型、Liberty、LEF、GDS，以及固定 ORFS 镜像中的 SG13G2 标准单元和提取视图。IHP 文件采用 Apache-2.0，版权归 IHP PDK Authors；原文件的版权和许可说明随文件保留。

SRAM 上游提交固定为 `5e6d592e4002946a4616f798c357f0f3c06cf3b6`。ORFS 平台视图按容器摘要和文件摘要标识，见 [SPM 验证记录](reports/SPM.md)。

Verilator 网表功能回放使用派生的 `sg13g2_stdcell_functional.v`：显式连接 specify 的延迟参考信号，并为触发器输出添加 10 ps 延迟。证据包保留原模型、派生模型与各自摘要。

算术物理流程使用 [ASAP7](https://github.com/The-OpenROAD-Project/asap7) RVT 标准单元。布局布线后网表回放使用从实际 Liberty 逻辑函数生成的 `cells.v`。这些标准单元资料采用 [BSD 3-Clause 许可](licenses/ASAP7.txt)，版权归 Lawrence T. Clark、Vinay Vashishtha 或 Arizona State University。证据包保留模型、源 Liberty 文件摘要、固定工具镜像摘要与许可文本。

## 数值参考工具

[Berkeley SoftFloat](https://github.com/ucb-bar/berkeley-softfloat-3) 和 [Berkeley TestFloat](https://github.com/ucb-bar/berkeley-testfloat-3) 由测试脚本下载到构建目录，采用上游 `COPYING.txt` 中的三条款 BSD 许可，版权归 The Regents of the University of California。它们作为独立测试参考，Python wheel 与 Chisel JAR 使用 Zircon-ASIC 自身的算术实现。

非线性算子使用 [MPFR](https://www.mpfr.org/) 经 gmpy2 提供独立高精度参考，并使用 [Sollya 8.0](https://sollya.org/) 离线生成和验证定点多项式。Sollya 采用 CeCILL-C 许可。这些工具由开发环境单独安装；分发资源包含本项目的生成脚本、数值常数和验证记录。

## 构建与运行依赖

Python 依赖 NumPy，可选依赖 Numba；Chisel 库依赖 Chisel 与 ujson。这些依赖由包管理器单独安装，许可文件随各自发行包提供。

RTL 和物理验证使用 Verilator、Icarus Verilog、OpenROAD Flow Scripts 及对应工艺平台。工具版本、平台来源和实际文件摘要记录在构建 manifest 与验证报告中。

分发的 ORFS 平台脚本遵循上游 [构建与运行脚本许可证](licenses/OpenROAD-flow-scripts.txt)。数值参考的原始许可文本见 [SoftFloat](licenses/softfloat.txt) 和 [TestFloat](licenses/testfloat.txt)。
