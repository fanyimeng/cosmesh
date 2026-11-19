# Thread 可见范围

一个 Thread 归一位所有者。私有仅本人，指定可见可选择一个或多个人，全员可见面向当前 Hub 的认证用户；查看者不取得写入权。个人同步独立设置。

```sh
cosmesh thread access THREAD_ID --visibility restricted --to USER --json
cosmesh thread access THREAD_ID --visibility public --json
cosmesh thread access THREAD_ID --visibility private --json
cosmesh thread browse THREAD_ID --records --json
cosmesh thread browse THREAD_ID --files --json
```

--to 可重复，名单为空归为私有。access 不带 visibility 查询当前状态，--expect-acl 可作并发版本检查。发布完整选定 Thread 的记录与可用历史，文件需明确选择；旧只发简报的授权不自动扩展。

本人按设备和 Agent 登记 Project、目录和 Session。用 thread associate ID 预览摘要，发布时在 thread access ID --visibility public 或 restricted 命令中加 --share-projects，只批准本次的 Agent、Project 类型和显示名称。设备标识、主机名、原生 ID、目录和会话保留在本人视图。旧快照维持原范围，私人变动不推进共享版本。

personal asset add THREAD_ID FILE 登记文件，thread access ... --files 或 thread sync ID --files 传输。科研数据、压缩包、二进制、未知后缀可存储，不自动解包或执行；凭证及特殊路径保留过滤。对外单文件2 GB、每批5 GB、单Thread总容量15 GB（含历史），GB为10⁹字节。相关 Library 内容计入来源容量。

查看者默认在线阅读，--record/--version 看固定历史，--snapshot 指定快照；--asset ID --output NEW_PATH 明确下载。逐请求与分块检查当前权限，已有下载件和本人笔记保持。

thread track 按人建议本人私有追踪 Thread，明确 --use/--create 后建立连接；清单只包括该人指定给本人的工作，排除全员可见。身份随稳定 ID，名字可改变。首次有效新来信按同一连接自动归入私人 Thread，无登记且无重名才创建；消息检索与增量读取见 guide show chat。

thread share 已退役。旧公共内容由管理员以 hub migrate-threads 离线迁移，原资料及备份保留；迁移后的 Hub 拒绝旧程序与旧整库读取。迁移和回退见 guide show operations。权限更新不表示已发通知。
