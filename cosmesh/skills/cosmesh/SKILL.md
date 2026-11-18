---
name: cosmesh
description: 管理本人 Thread、跨设备接续、按人追踪与按权限在线查看；Library 资料继承来源权限。
---

## 一个 Thread，一位所有者

Thread 有私有／指定可见／全员可见三种范围，个人同步独立设置。本人多设备、多 Agent 沿用原 ID，项目、目录和会话分别登记。其他用户获得查看权限，自己的工作和反馈记录到本人 Thread。复用已有合适 Thread；新建、绑定、改权限和发布按用户授权执行，首次收取有效新消息会自动建立无重名的私人按人 Thread；其余工作按明确授权执行。

明确工作目标后直接使用已知 ID；缺少才一次 thread locate。本人本地总览 thread page --ready --json；记录进展 thread update ID --file FILE --interaction-summary TEXT --ready --json。已授权同步的新访问 Thread，再 thread sync ID。edit 使用 --expect，重试沿用原输入／操作键。只读取任务必需材料，不扫描聊天数据库或整个目录，不重复查成功回执。

## 个人同步与恢复

thread personal ID 主动保存选定 Thread，完整记录、可用历史和本人关联仅本人可取。thread list --personal --remote 查看远端；thread fetch ID --workdir DIRECTORY --agent AGENT 恢复原 ID 并登记目的地。真实原生项目可加 --project-id/--project-name，目录工作区使用实际目录。后续 thread sync ID，--pull 只拉取；thread locations ID 展开关联。设备／目录登记与实际执行分别报告。

明确选择文件用 personal asset add ID FILE [--as RELATIVE_PATH]，thread sync ID --files 上传选定文件，fetch 加 --files 恢复。已有不同内容保留并报告冲突；冲突时可选新本地数据根 --root，原数据与缓存保留。不要自动覆盖或删除旧副本。

## 可见范围与在线查看

thread access ID 查询当前权限。指定查看者用 thread access ID --visibility restricted --to USER，可重复 --to；全员用 public，仅本人用 private。迁移后若 sync 返回 review_required，先展示保留的私人历史／文件范围，沿用户明确授权使用 thread access 发布；原公共版本保持。首次发布选定 Thread 完整记录与历史，文件需明确登记及 --files；说明范围，不沿用旧“仅简报”的授权扩大发布。修改名单使用 --expect-acl REVISION。成功授权不等于已发送通知。

thread browse [ID] 在线读；--direct 列指定给本人的来源，--owner USER 按人；--records 列记录，--record ID/--version VERSION 看历史，--snapshot SHA 固定快照，--files 列文件。明确下载才 --asset ID --output NEW_PATH，按 next_offset 分页。默认不全量恢复别人的 Thread，不自动执行文件。

thread track 提供按人追踪建议，只包含对方指定给本人的 Thread，排除全员可见。优先复用本人同名 Thread，授权后 --use ID；确需新建 --create，默认名字为对方实名。连接按 Hub 和稳定用户 ID，改名不重复创建。可 --dismiss 忽略建议。show/context/continue 或 track --show ID 在线刷新来源；撤权后来源移除，本人笔记保留。离线仅展示已保存本人资料，不声称旧权限仍有效。

## 消息与增量检索

thread send OWN_THREAD --to USER --body TEXT 明确发送，request 是请求类型；thread reply MESSAGE_ID --thread OWN_THREAD --body TEXT 在本人 Thread 回应。需要先有来源查看权限，发送不自动授权。--local-only 保存草稿；相同输入／操作键重试，只有 accepted/sent 回执才称已发送。消息只引用选定记录，个人同步不顺带公开其他草稿。

thread inbox 默认消息，outbox 查看本人发出，chat --with USER 查看双方对话；mail show MESSAGE_ID 分页原文，mail mark MESSAGE_ID --seen 明确标已读，列信和搜索不标读。首次来信按稳定用户 ID 复用私人追踪 Thread，无重名时自动创建；同名未登记则提示选择，不能仅凭姓名合并。另一设备恢复原 ID，来源工作仍在线读取，定向工作清单排除全员可见内容。历史来信不触发批量创建。

thread search QUERY 搜索当前可见 Thread，--messages 搜索本人收发消息，--with USER 筛消息往来。结果给固定来源，可用 browse --snapshot/--record/--version 或 mail show 获取原文。thread browse ID --changes REVISION 仅列变动，后续页固定返回的新 --snapshot，完成后推进本地游标。索引按记录版本增量刷新，撤权即时过滤。完整说明见 guide show chat。

## Library 继承来源

有复用价值的文件用 library add --title TITLE --file FILE --thread THREAD_ID --sync，多个来源可重复 --thread。所有历史版本、搜索、目录、原件及提取文本都继承来源 Thread 的当前权限；多个来源取共同允许范围。查看者不能扩大来源权限。

独立面向全员的资源需用户明确授权后 --public；从已有来源独立公开须拥有全部来源。library revise 保留来源关系，修改用 --expect VERSION。library list/search/show/history/text 在线核验；--offline 仅查看已下载缓存，撤权不删除历史下载件。原件用 library fetch ID --name NAME --output NEW_PATH，固定引用用 library open cosmesh://library/K-ID@VERSION/FILENAME；--launch 仅在明确要求打开且文件适合查看时使用。

个人文件默认5 GB/份、10 GB/批、本人服务端总容量30 GB；对外2 GB/份、5 GB/批、单Thread总容量15 GB（含历史）。GB按十进制10⁹字节计算。Library单项最多32份文件，继承资料计入来源 Thread 容量。原未改动默认额度更新为本版限额，自定义不同值保留；服务器和本地额度取较小值。科学数据、压缩包、二进制及未知后缀允许原件存储，凭证及特殊路径仍过滤；目录不递归上传，文件不自动解包或运行，大文件不做全文提取。大小未实测到上限时不声称完成实测。

## 旧版本迁移与回退

新 CLI 的 thread share 已退役，统一使用 thread access；旧整库 personal connect/sync/join、asset push 保留替代提示。旧公开内容经管理员离线迁移后，保留原 ID、作者、贡献和固定历史；Library 同步建立来源权限。迁移前旧 Hub 保留原行为，不能把旧公开内容说成已缩权。管理员需停止所有网关，执行 hub migrate-threads --hub-root PATH 预览，明确授权后 --apply --gateways-stopped。个人 Agent 不自行进行 Hub 管理。

迁移保存原仓库、Library、私人流和独立备份，失败保持维护状态可重试。迁移后的 Hub 拒绝旧程序服务，防止回退重新暴露已收窄的内容；回退程序时服务暂停，恢复新版继续。备份可离线恢复核验，恢复旧服务须单独审阅当前权限，不自动恢复旧访问范围。原缓存保留，属于已下载历史资料。

旧公共 Git 整库读写、旧版在线消息与公共成品接口在迁移后关闭。历史消息作为 Thread 记录保留，可在线 browse；新消息使用 thread send/reply，在本人 Thread 中记录并按接收者权限投递，只有 accepted 才称已发送。私人反馈写入本人 Thread，引用来源按实际授权处理。

## 本人账号、宿主与入口

user me/list/show 读取实名目录；user set-name NAME --expect VERSION 只修改本人真实姓名。Profile 联系方式与偏好保持私人。服务器 DSH 是本人个人客户端；发布源负责软件分发，Hub 负责中转与权限，三者分别配置。client hub --service FILE 设置默认 Hub，已有显式连接保持。

本 Skill 末尾安装器记录实际 CLI 入口。普通业务不先查更新或诊断；明确更新才 client update。升级后完整展示 update_notice.document 并提示新开 Agent 会话。安装器替换托管 Skill，自定义文件和旧程序按原冲突保护／回退规则保留。
