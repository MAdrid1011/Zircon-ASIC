# 验证与复现

数值参考、Python 周期模型、Chisel 数据通路分别实现。共同契约共享格式和周期语义，不将任意一端的实现代码作为另一端的独立数值参考。

## 数值回归

`tests/test_softfloat.py` 对 FP16/FP32 的四个算子、五种舍入方式检查随机位模式和边界值笛卡尔积。生产 Python、Numba 和固定提交的 SoftFloat 均逐位比较结果与五类异常标志。SoftFloat 仅存在于忽略提交的测试构建目录，RISC-V specialization 提供规范 NaN。

`tests/rational_reference.py` 以 Python `Fraction` 和可表示编码的有序搜索独立验证 FP4 全输入与 FP8 随机输入。`scripts/small_oracle.cpp` 使用 128 位整数有理数和编码搜索完成大规模穷举；其结果先经过 Fraction 覆盖的测试交叉核对，再用于 Numba 与 RTL 的全输入检查。

每种 FP8 的每个二元算子包含 `256×256×5=327680` 个用例；每个 FMA 包含 `256³×5=83886080` 个用例。FP4 分别为 1280 与 20480。分片报告记录 `[begin,end)`、分片编号和总数，只有覆盖完整范围的集合才能视为全输入验收。

## 周期回归

`scripts/validate_rtl.py` 在每次运行前持久化全部输入和期望行为。驱动器在低电平设置端口并观察，再提交上升沿，符合共同契约的拍边界。逐拍比较 `in_ready`、`out_valid`、`stageValid`、`occupancy`、`phase`、`iteration`，在输出有效时比较全部响应字段，包括阻塞拍。

测试包含连续请求、随机气泡、持续长背压、满载、排空、复位和清空，记录每笔接收、首次可见和交付时刻。结果出现差异时保留 `stimulus.txt`、`python.trace`、`rtl.trace` 和含种子的 `failure.json`。不同 RTL 版本不得复用同一条验收记录。Verilator 可执行文件缓存同时绑定 RTL、驱动器的 SHA-256 与工具版本；源文件变化会强制重新编译。

`scripts/validate_network_rtl.py` 检查组合网络；`tests/test_network.py` 还验证插入顺序无关、Python/Numba 轨迹一致和跨后端状态延续。

## 物理评估

`scripts/ppa.py` 使用固定摘要的官方 OpenROAD Flow Scripts 镜像，默认 ASAP7 RVT 的 TC/0.70 V/0 °C，WC/0.63 V/100 °C 作为压力测试。时钟周期为 1000 ps，输入与输出延迟各 200 ps，时钟不确定度为 50 ps。

每次新物理运行冻结 RTL 内容，以 SHA-256 建立独立结果目录。共同评估级别为全局布线后的估算 RC 静态时序分析，要求 setup/hold 裕量均非负。详细布线结果按提取 RC 进行静态时序分析，并在配置表中标明物理阶段。面积引用标准单元实例面积，同时保留布局和布线产生的缓冲器影响。

物理流程使用 OpenROAD 完成实现与 STA，数值和周期行为通过独立参考、Chisel 断言及 RTL 回归检查。报告分别记录仿真、时序和形式检查状态；该流程的 post-resize LEC 设置为关闭。

结构选择以正确性、1 GHz 和指定吞吐为约束；在通过的候选之间比较面积乘无背压最坏延迟，差异不足 5% 时优先面积较小者。逐配置状态见 `reports/CONFIGURATIONS.md`。

## SPM 验证

独立字节数组参考检查读写与字节掩码。Python、Numba 和 RTL 逐拍比较全部端口的握手、响应、轮询指针、返回阶段、队列占用与实际写入事件。宏包装器使用 IHP 官方功能模型检查全部 16 种字节掩码、覆盖写、连续读写和停用状态。

| 配置或场景 | 回归 |
|---|---|
| 4 KiB / 32 bit / 1 bank / 1 请求端，IHP 宏 | 5 个种子，每种子 100,000 拍随机刺激，另含初始化与读回 |
| 16 KiB / 32 bit / 4 banks / 4 请求端，IHP 宏 | 5 个种子，每种子 100,000 拍随机刺激，另含初始化与读回 |
| 通用同步存储配置 | 每配置 5 个种子，每种子 20,000 拍随机刺激 |
| 读 SPM → INT32Mul → 写 SPM | Python／RTL 逐拍比较，含加载和读回共 20,155 拍 |
| Python／Numba | 轨迹、状态、存储内容和跨后端连续运行检查 |

两个宏配置使用 IHP SG13G2：100 MHz，输入／输出最大延迟各 2 ns、最小延迟 0 ns，setup 不确定度 0.5 ns、hold 不确定度 0.1 ns。TT 1.20 V / 25°C 和 SS 1.08 V / 125°C 均执行详细布线后提取 RC 的 setup、hold、时钟脉宽和宏时序检查。

综合网表通过官方 SRAM 模型进行功能回放。Verilator 标准单元功能模型显式连接 specify 延迟参考线，并在 Q 输出加入 10 ps 延迟以处理零延迟时钟树的事件顺序；原文件与适配文件分别记录摘要。时序测量使用独立 STA。完整运行入口与 CPU 性能见 [SPM 文档](hardware/spm.md) 和 [SPM 报告](../reports/SPM.md)。

## 测试工具与来源

- [SoftFloat](https://github.com/ucb-bar/berkeley-softfloat-3)：测试用参考，固定提交见 `scripts/build_oracles.py`。
- [TestFloat](https://www.jhauser.us/arithmetic/TestFloat-3/doc/testfloat_gen.html)：系统化测试向量工具，固定提交并本地构建。`scripts/testfloat_vectors.py --rtl` 默认对每种舍入方式使用 level-1 流的前 2048 项，覆盖全部 FP16/FP32 算子；报告记录种子、实际数量和向量哈希。
- [Chisel](https://www.chisel-lang.org/docs/explanations/interfaces-and-connections)：7.15.0，firtool 1.158.0；使用原生 Scala/Chisel 数据通路。
- [OpenROAD Flow Scripts](https://openroad-flow-scripts.readthedocs.io/en/latest/user/DockerShell.html)：容器摘要固定在 `scripts/ppa.py`。
- [OCP FP8](https://www.opencompute.org/documents/ocp-8-bit-floating-point-specification-ofp8-revision-1-0-2023-06-20-pdf) 和 [MX 格式](https://www.opencompute.org/documents/ocp-microscaling-formats-mx-v1-0-spec-final-pdf)：编码背景。本库的标量算术、饱和和异常契约另行明确。

## 报告与状态失效

`python scripts/collect_reports.py` 将测试、穷举、逐周期、性能和物理记录汇总为 `reports/validation.json`，同时生成配置表和包内 `qualification.json`。完整 Python 回归记录实际源内容的 SHA-256；RTL 记录绑定生成内容的 SHA-256。验证状态按实现、契约和 RTL 摘要匹配；更换实现或自定义时序后重新建立相应验证记录。

`describe()` 区分 `unqualified`、`cycle-verified` 与 `dual-verified`，并给出每项门槛及物理评估级别。硬件生成的 manifest 默认保持未合格，使用其中的 RTL 哈希与发布配置表关联验收证据。

## 仿真器交叉核对与版本兼容

Verilator 5.022 之前的版本使用 `-fno-const-bit-op-tree -fno-expand`，兼容其位运算和移位优化行为。验证脚本按版本自动设置选项，并将其写入构建标识。相关上游修复见 [NOT 位运算优化修复](https://github.com/verilator/verilator/pull/4847) 和 [移位宽度修复](https://github.com/verilator/verilator/pull/4849)。这项兼容处理不改变 Chisel 或生成的 RTL。

`python scripts/replay_iverilog.py build/rtl/fp16_fma` 可用独立 RTL 仿真器重放保存的刺激和 Python 期望轨迹。CI 保存 RTL、manifest、刺激和双边轨迹。发生差异时，诊断归档记录种子、首个分歧周期和对应实现摘要。
