# Thread 的创建、记录与接续

Thread 是持续工作的稳定标识，由一位用户所有。本人多个设备、Agent、项目、目录与会话可关联同一 Thread。私有／指定可见／全员可见只决定谁能查看，个人同步另行开启。

先复用已知 ID；未知时 thread locate 按真实 Agent、项目 ID／名称、目录或 Session 定位。明确接入时选择合适已有 Thread，缺少才 create；多个合适候选先厘清。name 支持英文名、中文名、简介与别名，稳定 ID 不变。

thread create --title TITLE --body TEXT 新建本人工作。note/result/summary 追加普通记录；update ID --file FILE --interaction-summary TEXT --ready 记录增量。独立标题、简洁摘要、详细正文和实际作者分别保存。无新信息不重复写，编辑使用固定 --expect，重试沿用操作键。

bind-project 关联实际宿主项目，缺原生项目使用 --kind directory；bind 关联真实目录与会话。不造项目／会话 ID，不读取聊天数据库。thread personal/sync/fetch/locations 管理本人跨设备接续，原 ID 和各端登记保留。

本人按设备和 Agent 登记 Project、目录和 Session。用 thread associate ID 预览摘要，发布时在 thread access ID --visibility public 或 restricted 命令中加 --share-projects，只批准本次的 Agent、Project 类型和显示名称。设备标识、主机名、原生 ID、目录和会话保留在本人视图。旧快照维持原范围，私人变动不推进共享版本。

没有关联的旧 Thread 使用 thread associate --missing 查看；thread associate THREAD --host-file OBSERVED.json 只读生成候选，只有唯一有效证据才允许 --confirm CANDIDATE --expect REVISION。可另加明确选择的 --journal FILE，但仍须宿主重新核验。标题、generation.tool、目录相似或同名不单独构成关联；预览不写入，确认使用稳定幂等键，歧义先手工厘清。

thread access 管理查看权限；thread browse 按需在线读取他人的原记录、历史和文件。查看者的笔记与成果写入本人 Thread，保留来源身份。thread track 按人建立本人私有追踪入口，优先复用，排除该人的全员可见工作。

split/merge/delete/restore 继续按明确授权保留来源、旧 ID 和历史。旧公共 Git 缓存保留作离线历史；迁移后的所有者通过 fetch 恢复本人内容，再维护与同步。权限和来源资料见 sharing、personal、knowledge；迁移回退见 operations。
