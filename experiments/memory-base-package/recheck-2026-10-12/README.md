# #86 独立安装 R2

状态：预注册，尚未构建或运行。对象为 #89 修复后的冻结 `9252d5281ab69ad418c76e1672af4dc6fbdff9e5`，判据见 [PREREGISTRATION.md](PREREGISTRATION.md)。原目录的 FAIL、首次环境错误和所有 store 保持原样。

本轮将区分借正式依赖的 launcher 和真实完整安装 pinned 依赖的全新目标 runtime；native 结果与合成协议结果分开。资产留在本目录 ignored store，禁止清理或归档唯一基座。
