# 验证与复现

数值参考、Python 周期模型、Chisel 数据通路分别实现。共同契约共享格式和周期语义，不将任意一端的实现代码作为另一端的独立数值参考。

## 数值回归

`tests/test_softfloat.py` 对 FP16/FP32 的四个算子、五种舍入方式检查随机位模式和边界值笛卡尔积。生产 Python、Numba 和固定提交的 SoftFloat 均逐位比较结果与五类异常标志。SoftFloat 仅存在于忽略提交的测试构建目录，RISC-V specialization 提供规范 NaN。

`tests/rational_reference.py` 以 Python `Fraction` 和可表示编码的有序搜索独立验证 FP4 全输入与 FP8 随机输入。`scripts/small_oracle.cpp` 使用 128 位整数有理数和编码搜索完成大规模穷举；其结果先经过 Fraction 覆盖的测试交叉核对，再用于 Numba 与 RTL 的全输入检查。

每种 FP8 的每个二元算子包含 `256×256×5=327680` 个用例；每个 FMA 包含 `256³×5=83886080` 个用例。FP4 分别为 1280 与 20480。分片报告记录 `[begin,end)`、分片编号和总数，只有覆盖完整范围的集合才能视为全输入验收。

## BF16 回归

`scripts/validate_bf16.py --rtl` 使用 `tests/rational_reference.py` 的独立整数有理数参考，覆盖五种舍入方式。每个操作在每种舍入方式下遍历 65,536 个按仿射置换生成的输入编码，并加入 20,000 组随机输入和特殊值、抵消、边界指数及除法商余定向用例。最终向量数分别为 add 435,465、mul 435,465、FMA 662,265、div 517,385；Python、Numba 和 RTL 的结果位、五个标志位和余数均逐项比较。

`scripts/bf16_alignment.py` 对每个 BF16 单元运行五个 20,000 拍随机背压、reset、flush 种子和一个 100,000 拍种子。BF16 乘法还对直接 Dadda、Booth-Dadda 与原生乘法三种 8×8 有效数核心穷举 65,536 个输入对。四个最终配置均通过 ASAP7 RVT 的 TC 0.70 V / 0 °C、1 GHz 详细布线与提取 RC 检查；路由审计检查 setup、hold、时钟约束、未约束端点和 DRC。

## 非线性算子回归

`scripts/validate_sfu.py` 使用 MPFR 4.2.2 的有向上下界建立独立参考。参考从 128 bit 开始增加精度，直到目标舍入结果可确定；RMM 显式处理中点与远离零规则，rsqrt 直接使用 `rec_sqrt`。FP16、BF16 各穷举全部 65,536 个输入，exp 检查 RNE，其余三个算子分别检查五种模式，共 2,097,152 组输入／舍入组合。FP32 每算子至少一百万个分层输入，额外覆盖表段边界、类别阈值、完全平方数和近舍入中点。

`scripts/certify_sfu.py` 重新生成 exp 系数和误差证据，检查逐级量化、定点截断、分段连接与类别阈值。FP32 rcp 穷举 8,388,608 个规格化有效数，sqrt、rsqrt 各检查 16,777,216 个有效数／指数奇偶组合，验证固定校正距离及精确性信息。低精度表逐项核对整数商余、平方比较与余量位。

`scripts/replay_sfu.py` 将数值向量送入 RTL，比较编码和异常，并检查每笔请求的完整延迟与 II=1。`scripts/sfu_alignment.py` 为每个配置保存五个固定种子各 20,000 拍以及一组 100,000 拍的 Python／Numba／RTL 轨迹。`scripts/validate_sfu_network.py` 检查三种格式的 SPM → exp → SPM 和 SPM → rsqrt → mul → SPM，覆盖最终存储内容和中途后端切换。

数值生成器需要开发依赖 `gmpy2`，系数生成使用 `scripts/sollya.Dockerfile` 固定的 Sollya 8.0；它们均不属于模拟器运行依赖。

```bash
python -m pip install '.[fast,test,verify]'
docker build --platform linux/amd64 -f scripts/sollya.Dockerfile -t zircon-asic-sollya:8.0 .
python scripts/validate_sfu.py
python scripts/certify_sfu.py
python scripts/replay_sfu.py --unit fp32.exp --unit fp32.rcp --unit fp32.sqrt --unit fp32.rsqrt
python scripts/replay_sfu.py
python scripts/sfu_alignment.py
python scripts/validate_sfu_network.py
python scripts/benchmark_sfu.py
```

`scripts/sfu_candidates.py` 组织数值筛选、综合、全局布线和详细布线候选。`scripts/check_sfu_physical.py` 审核提取 RC 后的 setup、hold、时钟、约束与路由结果；`scripts/replay_asap7.py` 使用对应 Liberty 的功能模型回放数值与周期向量，记录布线网表和 RTL 的轨迹等价结果。`scripts/collect_reports.py` 同时生成配置清单、非线性算子报告和机器可读证据。

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

`describe()` 返回配置对应的验证状态、验收门槛和物理评估级别。硬件生成的 manifest 使用 RTL 哈希与发布配置表关联验收证据。

## 仿真器交叉核对与版本兼容

Verilator 5.022 之前的版本使用 `-fno-const-bit-op-tree -fno-expand`，兼容其位运算和移位优化行为。验证脚本按版本自动设置选项，并将其写入构建标识。相关上游修复见 [NOT 位运算优化修复](https://github.com/verilator/verilator/pull/4847) 和 [移位宽度修复](https://github.com/verilator/verilator/pull/4849)。这项兼容处理不改变 Chisel 或生成的 RTL。

`python scripts/replay_iverilog.py build/rtl/fp16_fma` 可用独立 RTL 仿真器重放保存的刺激和 Python 期望轨迹。CI 保存 RTL、manifest、刺激和双边轨迹。发生差异时，诊断归档记录种子、首个分歧周期和对应实现摘要。
