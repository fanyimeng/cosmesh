# 查找工作与资料

thread search QUERY 搜索当前有权读取的 Thread 正文；--messages 搜索本人收发消息，--with USER 筛消息往来。索引按固定记录版本增量更新，结果带来源 Thread、快照、记录和版本，撤权后即时过滤。已知消息用 mail show；已知 Thread 用 browse 按固定版本展开。thread browse ID --changes REVISION 列内容变动，详情见 chat 主题。

当前有权查看的远端工作用 thread browse [--owner USER] [--direct]，按 next_offset 分页，选定 ID 再按需展开记录。thread track 汇总对方明确指定给本人的来源，排除全员可见。

library search QUERY 搜索当前允许读取的资料；原件、提取文本、历史版本和目录均继承来源 Thread 的当前权限。library show/history/text 读取相应材料，明确下载才 fetch/open。离线读取使用 --offline，旧缓存表示已下载历史，不代表当前权限仍然有效。

search query QUESTION --query VARIANT 是本人本地及历史缓存检索，--scope private/public/all 控制本地来源，--kind thread/record/knowledge 和 --limit/--offset 筛选；它不自动发现未获取的远端 Thread。已有固定 ID／回执直接使用，缺口才搜索，不全量遍历资料。

本地旧格式来源保留原 ID、作者、版本与 repo 标识，默认只读查证，不自动迁移或重新发布。结果引用仍按来源权限处理；私人 Profile、聊天数据库及项目目录不属于搜索范围。
