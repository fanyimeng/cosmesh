# 🚀 CosMesh 0.5.0｜Thread 的执行位置成为一手元数据（候选）

**设备、Agent、Project 现在有统一的 execution_revision 和两种视图；本候选尚未正式发布。**

- **Project 摘要随 Thread 可见**：只要能读取 Thread，就能读取 device／host、Agent、Project 类型／ID／名称及登记／观测状态；撤权后同步关闭。
- **绝对目录与 Session 仍仅本人可见**：workdir、Session ID／标题、恢复 URL、journal、handoff 和操作回执不进入 shared。个人同步继续恢复完整 owner 元数据。
- **旧 Thread 可补关联**：`thread associate --missing` 查找未关联工作；`thread associate THREAD --host-file observed.json` 只读预览，只有唯一有效证据才允许确认。标题、目录相似或 generation.tool 不单独构成关联。
- **跨设备视图统一**：overview、page、list、browse、locations、fetch 和 sync 使用同一 execution_revision；project/workspace 重复登记与目录回退会确定性归并，歧义不会被隐藏。
- **Connector 独立候选 0.1.2**：保留旧参数和回执，新增 receiptVersion／executionRevision／executionMetadata；旧 CLI 对应字段为空。CLI 升级不自动安装或变更插件。

**升级注意：新版在线接口会按当前 Thread 权限返回已有的设备／Agent／Project 摘要，Thread 的权限名单本身不变。发布前请按实际可见范围审查现有摘要；绝对 workdir 和 Session 不会因此公开。**

**请新开 Agent 会话加载更新后的 Skill。** 重看说明：guide show upgrade。
