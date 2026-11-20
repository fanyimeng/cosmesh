# 主要模块

- work_directory.py 管理按所有者隔离的名称索引、工作发现和收件关联建议。
- collector.py 管理每个执行节点明确登记的目录、工作检查点、离线待传与宿主摘要。
- thread_personal.py 和 thread_personal_protocol.py 管理单项工作的版本、增量文件与三方合并，personal_relay.py 提供按账号隔离的存储。thread_locations.py、thread_execution_model.py 处理原生执行位置。
- deliveries.py 保存固定版本的内容投递、参与者的私人归属和检索索引。delivery_cli.py 实现预览、选取正文／附件、收发件箱和按需下载。
- thread_migration.py、thread_access.py 及 library_access.py 保留旧资料的来源和权限检查。普通工作更新和新版投递通过各自独立的存储处理。
