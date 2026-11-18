# Library：资料与来源权限

Library 保留资料 ID、作者、版本、原件、提取文本与贡献。资料默认继承指定来源 Thread 的当前权限；多个来源取共同允许范围，全部历史版本统一检查。撤权后目录、搜索、固定历史、原件与提取文本停止在线提供。

```sh
cosmesh library add --title TITLE --file FILE --thread THREAD_ID --sync --json
cosmesh library list --json
cosmesh library show K_ID --json
cosmesh library history K_ID --json
cosmesh library fetch K_ID --name FILENAME --output NEW_PATH --json
```

--thread 可重复。独立面向全员资料需明确 --public；从来源独立公开须拥有全部来源。revise 用 --expect VERSION，保留来源关系；不能通过删引用、换版本或移交维护人取消继承。原件始终按调用者当前权限读取，知道对象摘要不等于具有读取或上传所有权。

默认单文件2 GB、单项最多32份、合计5 GB；来源 Thread 总容量15 GB（含历史），Library 原件和历史计入。科学数据、压缩包、程序二进制、未知后缀均可作原件存储。凭证与特殊路径继续过滤。上传、下载逐块校验；超过20 MiB的原件不做全文提取，存储成功与可检索全文分开报告。

固定引用为 cosmesh://library/K-ID@VERSION/FILENAME。library open 验证并准备本机副本；大文件使用流式复制，明确使用原件，不自动启动执行。library fetch 保留已有目标。在线命令刷新当前权限；--offline 只展示已下载的历史缓存，撤权不会删除已下载文件。

旧 Library 的版本与原件在 Hub 迁移中保留，并从原来源引用、资料索引和归档登记继承；原无来源的独立公共资料保持全员可见。迁移后旧全库快照与哈希读取无法绕过当前权限。
