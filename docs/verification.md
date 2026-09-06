# 验证与复现

数值参考、Python 周期模型、Chisel 数据通路分别实现。共同契约共享格式和周期语义，不将任意一端的实现代码作为另一端的独立数值参考。

## 数值回归

`tests/test_softfloat.py` 对 FP16/FP32 的四个算子、五种舍入方式检查随机位模式和边界值笛卡尔积。生产 Python、Numba 和固定提交的 SoftFloat 均逐位比较结果与五类异常标志。SoftFloat 仅存在于忽略提交的测试构建目录，RISC-V specialization 提供规范 NaN。

`tests/rational_reference.py` 以 Python `Fraction` 和可表示编码的有序搜索独立验证 FP4 全输入与 FP8 随机输入。`scripts/small_oracle.cpp` 使用 128 位整数有理数和编码搜索完成大规模穷举；其结果先经过 Fraction 覆盖的测试交叉核对，再用于 Numba 与 RTL 的全输入检查。

每种 FP8 的每个二元算子包含 `256×256×5=327680` 个用例；每个 FMA 包含 `256³×5=83886080` 个用例。FP4 分别为 1280 与 20480。分片报告记录 `[begin,end)`、分片编号和总数，只有覆盖完整范围的集合才能视为全输入验收。

## 周期回归

`scripts/validate_rtl.py` 在每次运行前持久化全部输入和期望行为。驱动器在低电平设置端口并观察，再提交上升沿，符合共同契约的拍边界。逐拍比较 `in_ready`、`out_valid`、`stageValid`、`occupancy`、`phase`、`iteration`，在输出有效时比较全部响应字段，包括阻塞拍。

测试包含连续请求、随机气泡、持续长背压、满载、排空、复位和清空，记录每笔接收、首次可见和交付时刻。结果出现差异时保留 `stimulus.txt`、`python.trace`、`rtl.trace` 和含种子的 `failure.json`。不同 RTL 版本不得复用同一条验收记录。

`scripts/validate_network_rtl.py` 检查组合网络；`tests/test_network.py` 还验证插入顺序无关、Python/Numba 轨迹一致和跨后端状态延续。

## 物理评估

`scripts/ppa.py` 使用固定摘要的官方 OpenROAD Flow Scripts 镜像，默认 ASAP7 RVT 的 SS/0.63 V/100 °C。时钟周期为 1000 ps，输入与输出延迟各 200 ps，时钟不确定度为 50 ps。这是一组公开记录的评估条件，不是代工厂流片签核。

每次新物理运行冻结 RTL 内容，以 SHA-256 建立独立结果目录。合格判断要求布线后 setup/hold 裕量均非负；不会把脚本退出码为零或“综合完成”当作 1 GHz 通过。面积引用标准单元实例面积，同时保留布局和布线产生的缓冲器影响。

macOS ARM 上的固定镜像通过 AMD64 仿真运行。镜像附带的 Kepler 形式工具发生了非法指令错误，因此物理脚本关闭自动 post-resize LEC，并在报告显式记为未执行；算术参考、Chisel 断言和 RTL 周期回归仍独立执行。该限制不应被描述为形式等价验证通过。

初始级数只是候选。硬约束为正确性、1 GHz 和指定吞吐；在通过的候选之间比较面积乘无背压最坏延迟，差异不足 5% 时优先面积较小者。当前尚未完成所有候选的时序收敛和面积筛选，不提供“所有部件已最优”的声明。

## 测试工具与来源

- [SoftFloat](https://github.com/ucb-bar/berkeley-softfloat-3)：测试用参考，固定提交见 `scripts/build_oracles.py`。
- [TestFloat](https://www.jhauser.us/arithmetic/TestFloat-3/doc/testfloat_gen.html)：扩展测试向量工具；提交固定在构建脚本中，尚未作为当前测试生成器使用。
- [Chisel](https://www.chisel-lang.org/docs/explanations/interfaces-and-connections)：7.15.0，firtool 1.158.0；使用原生 Scala/Chisel 数据通路。
- [OpenROAD Flow Scripts](https://openroad-flow-scripts.readthedocs.io/en/latest/user/DockerShell.html)：容器摘要固定在 `scripts/ppa.py`。
- [OCP FP8](https://www.opencompute.org/documents/ocp-8-bit-floating-point-specification-ofp8-revision-1-0-2023-06-20-pdf) 和 [MX 格式](https://www.opencompute.org/documents/ocp-microscaling-formats-mx-v1-0-spec-final-pdf)：编码背景。本库的标量算术、饱和和异常契约另行明确。
