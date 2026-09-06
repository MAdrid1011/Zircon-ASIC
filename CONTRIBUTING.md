# 贡献指南

欢迎通过 [Issues](https://github.com/MAdrid1011/Zircon-ASIC/issues) 报告问题，或通过 Pull Request 提交改进。

## 问题报告

提供模块与配置、Python／Chisel 版本、最小复现输入和期望结果。周期问题请附请求、背压、reset／flush 序列及首个分歧周期；数值问题请使用原始位编码，并注明舍入方式。

## 修改与验证

从 `main` 创建分支，在源码目录执行 `python -m pip install '.[fast,test]'` 安装测试环境。使用 `python -m pytest` 运行 Python 测试；Chisel 与 RTL 的工具和运行入口见 [验证文档](docs/verification.md)。

接口、格式、流水线或调度规则变更应同时更新共同契约、Python 模型、Chisel 实现、测试与模块文档。运行受修改影响的数值和逐拍回归；物理结果按对应 RTL 摘要匹配。纯文档修改检查链接、示例和源码一致性。

Pull Request 说明实际问题、修改后的行为及验证结果。复现失败时保留随机种子和轨迹，方便检查。贡献按照项目的 [Apache-2.0 许可证](LICENSE) 提交。
