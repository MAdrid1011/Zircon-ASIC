# 普通 SPM

`SPM` 提供按字节寻址的片上存储接口。Python 模拟器、Numba 周期循环和 Chisel 使用 `data/spm.json` 中的共同契约。SPM 保存原始比特，不执行数值转换；地址生成和访问顺序由调用方控制。

## 配置与接口

默认配置为 4096 字节、32 bit 数据、一个 bank 和一个请求端。数据宽度支持 8/16/32/64 bit，bank 数支持 1/2/4/8/16，请求端支持 1～8 个。容量必须能被 bank 数与字节数的乘积整除。

请求包含 `address`、`write`、`data`、`mask` 和 `tag`。地址与标签为 32 bit 无符号值。一次请求访问一个自然对齐的数据字，小端序。写掩码每 bit 对应一个字节，1 表示更新；`mask=None` 表示全部字节。全零掩码仍消耗一次 bank 服务并返回写确认。

响应包含 `bits`、`tag`、`write`、`status`。成功读返回数据，写确认和错误响应的数据固定为 0。状态为 `OK`、`OUT_OF_RANGE`、`MISALIGNED`，先检查越界，再检查对齐。错误请求占用响应额度，但不访问 bank。

## 地址映射与选择

每个 bank 是单读写端口，每拍只能读或写一次。设数据字字节数为 W、bank 数为 B，则 `word=address//W`、`bank=word%B`、`row=word//B`。

请求没有输入队列。SPM 在响应额度足够的请求端中，按目标 bank 独立轮询选择请求；成功握手即表示获得本拍访问机会。读写无优先级，同地址读不合并。轮询指针初始为 0，只在实际接收后移动到获选端的下一端口。

没有请求时 `in_ready` 为低。上游不能等待 `ready` 才提出 `valid`；请求提出后，需要保持到握手或取消。公平性保证以请求持续有效且响应容量可用为前提。

## 访问阶段和响应缓冲

第 k 拍接收请求，在提交边沿访问 SRAM。第 k+1 拍将 SRAM 输出及元数据写入非直通响应队列，第 k+2 拍响应可交付。无冲突、无背压时每 bank 的 II 为 1。

每个请求端有两个未交付额度，覆盖 SRAM 返回阶段和响应队列。响应队列物理深度为 2，返回阶段另存有效位和元数据，但合计占用不能超过 2。本拍交付可以释放额度用于本拍新请求。SRAM 访问前已经预留响应空间，因此返回阶段不因下游突然停顿而丢失数据。

输出阻塞期间，响应有效位和全部字段保持稳定。同一请求端按接收顺序交付，不同端口没有全局交付顺序。读结果在访问时确定，后续写入不改变已经在途的读响应。

## 复位、清空和初始化

复位或清空优先于传输，取消访问阶段和未交付响应，清空轮询指针；已经生效的写入保留。写确认被取消不等于写入被撤销。时钟内的复位不回退模拟器时间；直接调用 `reset()` 则将周期和统计重新计数，`flush()` 保留累计计数。

上电存储内容未定义。Python 使用逐字节初始化诊断，未初始化读抛出 `UninitializedReadError`；Numba 内核诊断为异常。硬件不增加初始化位图，调用方必须先写入需要读取的内容。

`load_image(data, address=0)` 和 `dump_image()` 只能在无在途事务、无待提交求值时使用。`dump_image()` 返回全部 backing bytes，未初始化位置没有硬件数值语义。宏回归使用普通写请求初始化，不依赖复位清零。

## Python 使用

```python
from zircon_asic import SPM, MemoryRequest, Inputs

spm = SPM()
spm.load_image(bytes(4096))
spm.compute(MemoryRequest(0, write=True, data=17))
assert spm.compute(MemoryRequest(0)).bits == 17

assert spm.step(Inputs(MemoryRequest(0, tag=7))).accepted
spm.step()
result = spm.step()
assert result.delivered and result.response.bits == 17
```

功能模式的 `compute` 修改或读取当前存储，不推进周期。`compute_batch` 按数组顺序执行，不能用数组顺序表示同时访问。功能调用禁止与在途周期事务混用。

多端口使用 `spm.eval((inputs0, inputs1, ...))` 后调用一次 `spm.tick()`，或者一次 `spm.step(tuple_of_inputs)`。端口不能独立推进时钟。重复 `eval` 不修改架构状态，`tick` 提交最后一次求值。

`run(schedule, backend="numba", trace=True)` 支持预先给出的逐拍输入，包括气泡和背压；调用方仍须遵守未接收请求保持协议。`trace=False` 返回 `(cycle, port, response)` 交付记录。CPU 执行时间与硬件周期数分别统计。

## 组合网络

`source`、`sink` 增加 `port` 参数；`connect` 增加 `source_port`、`destination_port` 和 `mapping` 参数。端口 0 保持原来的字符串键，其他端口使用 `(name, port)` 键。`Field("bits")` 表示引用上游响应字段，映射中其他整数值是常量。

成功读响应可以直接供给算术模块；错误响应和写确认需要作为响应处理，不能连接到数据通路。写入目的地址及掩码必须显式映射。映射不执行自动地址递增、隐式截断或用户回调。

网络先快照旧输出并生成请求，再反向计算背压和完整 SPM 仲裁，最后每个模块提交一次。当前连接要求模块依赖图无环，多端口 SPM 的所有端口作为同一个状态所有者；包含同一 SPM 的组合反馈在依赖检查中报错。反馈架构应由用户的时钟状态控制器分拍驱动。

## Chisel 与 IHP 宏

`SPMConfig` 和 `SPM` 为公开配置与模块，每个端口使用 `Decoupled[MemoryRequest]` 与 `Decoupled[MemoryResponse]`。通用 bank 使用同步读写存储，IHP 配置每 bank 显式实例化 `RM_IHPSG13_1P_1024x32_c2_bm_bist`。

IHP 宏模式要求每 bank 为 1024×32 bit。字节掩码展开为 bit 掩码，关闭 BIST，`A_DLY` 固定为 1。宏内容不接复位。生成器与 JAR 共同携带 SPM 契约，生成结果记录配置与 RTL 摘要。

`SPM` 的观察端口提供仲裁指针、获选端、返回阶段、队列占用和写入事件，用于三方逐拍比较。物理实现使用 `SPMPhysical` 封装，只暴露正式访存接口；内部控制与 SRAM 实例相同。正式输入、输出全部按 2 ns 最大外部延迟约束，观察端口不作为芯片功能引脚。

轮询硬件先选择编号不小于当前指针的请求；这一集合为空时从端口 0 重新选择。选择使用独热向量驱动 bank 数据输入，避免在 SRAM 输入路径再次解码获选端编号。该优化不改变软件模型的轮询顺序。

运行 `scripts/fetch_ihp.py` 获取固定提交的官方模型和物理视图；`scripts/validate_spm.py --ihp` 执行对齐回归；`scripts/ppa_spm.py` 运行含宏 IHP 物理流程。面积时序结果只适用于报告中的实际配置、工艺与角条件。

SRAM 视图锁定 IHP PDK 提交，控制逻辑使用固定 ORFS 镜像中的 SG13G2 平台视图；两者分别记录摘要。报告分别记录上游 SRAM 文件和 ORFS 平台视图的来源与摘要。IHP 与原有算术 ASAP7 结果分开报告。

## 统计与验证状态

`stats.cycles` 是已推进周期数；`accepted`、`delivered` 和 `cancelled` 分别统计接收、交付与取消事务。`occupancy_sum` 累加每拍所有端口的未交付事务数，除以周期数可得到平均占用。`port_stats` 保留相同的逐端口统计。

`bank_accesses` 按 bank 统计真正获得服务的访问；`bank_conflicts` 按请求端统计响应额度足够但仲裁失败的等待拍；`credit_stalls` 统计额度不足的等待拍。`reads` 和 `writes` 按接收的操作类型计数，包括最终返回地址错误的请求。功能调用不修改这些周期统计。

`describe()` 返回当前参数、两拍无背压延迟、每 bank 的 II、响应额度及摘要匹配的验证证据。只有具体配置的功能、周期、性能和物理证据全部匹配时才显示 `dual-verified`。其他合法参数按各自的验证记录显示状态。

完整示例见 [组合与共享访问](../../examples/spm.py) 和 [用户时钟控制器](../../examples/spm_controller.py)。后者展示如何在同一个 SPM 上等待读响应，再由调用方发起写请求。

## 复现与构建

源码安装后，以下入口依次完成 Python、宏与三方对齐验证。RTL 验证需要 Java、sbt 和 Verilator；物理流程另外需要 Docker。基础模拟器安装不需要这些工具。

```sh
python scripts/validate_python.py
python scripts/fetch_ihp.py
python scripts/validate_spm_macro.py
python scripts/validate_spm.py --ihp --cycles 100000
python scripts/validate_spm.py --ihp --capacity 16384 --banks 4 --ports 4 --cycles 100000
python scripts/validate_spm_network.py
python scripts/benchmark_spm_compile.py
python scripts/benchmark_spm.py
python scripts/capture_ihp_platform.py
python scripts/ppa_spm.py
python scripts/ppa_spm.py --banks 4 --ports 4
```

对两个物理流程各自输出的目录，分别运行 `check_spm_physical.py <目录>` 和 `validate_spm_gate.py <目录>`。前者检查布线后两角时序与路由结果，后者回放综合网表；物理验证状态由这两项检查共同确定。完整命令、摘要与轨迹由 [SPM 验证记录](../../reports/SPM.md) 索引。

最后运行 `scripts/collect_spm_reports.py` 汇总当前摘要匹配的证据，运行 `scripts/package_spm.py` 生成本地证据包。Python 使用 `python -m build` 构建 wheel 与 sdist；Chisel 使用 `cd hardware && sbt package` 构建 JAR。构建产物版本为 `0.2.0`。
