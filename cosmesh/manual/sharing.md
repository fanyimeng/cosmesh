# 共享 Thread 与可打开的配套资料

默认只共享 Thread 文本简报／正式文本，不读取、不上传关联文件。用户明确要求附带关联文件，或经后续询问同意后，个人 Agent 才从已关联项目选择必要资料；程序先入知识库，再写固定引用并发布 Thread。使用现有知识资料、普通记录、版本与回链，不增加公共模型或整目录同步。

## 常用操作

普通共享直接使用 thread share THREAD_ID --sync；--summary-only 与默认相同。回执 upload_policy=explicit_only、state=not_requested 表示本次未新增关联文件；已有共享资料保留。有必要时可简短提示“关联文件尚未附带，可按需补传”，不固定逐条追问。以下附件参数仅在明确允许上传后使用。

```sh
cosmesh thread share THREAD_ID --attach /work/project/README.md --attach /work/project/report.html --attach /work/project/summary.json --sync --json
cosmesh thread share THREAD_ID --materials /work/share-selection.json --sync --json
cosmesh thread share THREAD_ID --attach /work/project/report.pdf --dry-run --json
```

--attach 可重复，默认列出的资料是必要文件；--materials 是私人选择清单，路径可相对此 JSON 文件，name 可给文件一个不含目录的公开名称，required=false 表示可选。清单只在本机使用，不上传路径。

```json
{"format":1,"files":[{"path":"project/README.md"},{"path":"project/report.html"},{"path":"project/summary.json","name":"audit-summary.json"},{"path":"project/cube.fits","required":false}],"code":{"url":"https://example.org/group/code","commit":"0123456789012345678901234567890123456789","paths":["scripts/tasks"]}}
```

--git HTTPS_URL --commit FULL_COMMIT [--code-path RELATIVE_PATH] 可直接登记代码；固定 SHA-1/SHA-256 commit，分支名不作为固定版本。仓库 URL 不含凭证或本机 SSH 映射；程序如实标明未代其他成员核验访问权，由个人 Agent 依据实际可用性判断。科研大数据只给说明／获取位置，默认不传。

正文提及本机路径也不会触发上传或阻止默认简报分享；共享文本标明范围，私人绝对定位省略。附件共享单独依据用户明确意图。资料分享使用 --sync，离线／预览不隐式联网。资料不足仍可明确分享概况，materials.state=partial 并列出未收录路径与必要文件缺项，不称完整交付。

## 限制与预检

单文件20 MiB，整次新原件合计100 MiB，最多32个入库文件；只处理显式选择，不递归扫描目录。默认跳过 FITS/FITS.GZ、HDF5、NPY/NPZ、MS/Zarr数据目录及ZIP/TAR/GZ等数据或压缩文件。PDF、Office和图像沿用现有支持。

UTF-8 文本按内容接收，支持 `.tex .py .md .bib .csv .tsv .json .yaml .yml .sh .c .h .cpp .html .css .js .toml .ini .rst .txt .log`，也支持其他后缀及无后缀文本。文本中的 NUL、二进制控制字符、非 UTF-8 内容以及二进制程序被拒。源码按资料存储，脚本的可执行权限不影响文本接收；打开缓存文件时使用私人普通文件权限。HTML/SVG 继续核对资源自包含条件。

跳过项返回 `name`、`reason`、`suffix` 和允许类型提示。数据/压缩后缀为 `data_or_archive_suffix`，程序后缀或内容为 `executable_suffix` / `executable_content`，无效文字为 `not_utf8_text` / `binary_content`；大小限制沿用原原因。必要文件被跳过时资料状态为 partial。

大小／后缀首先看名称与文件大小；数据文件不读取内容。HTML/SVG 静态检查资源依赖，优先提交自包含版本或PDF，不执行脚本或自动下载资源。Markdown 相对引用的报告／图片也应选入同一资料；打开时按原相对路径准备缓存副本。缺失依赖明确标记。

预检回执汇总 files/skipped/unresolved/bytes 和 limits；超限文件跳过并说明原因。必要文件跳过时状态 partial，可选大数据跳过可保留 ready。原件总量为本次预算，已入库资料的引用不重复计上传量。--resource FIXED_REFERENCE 可复用一个已入库固定文件。

## 固定引用与打开

共享正文和资料索引使用以下形式，固定资料版本与文件名：

```text
cosmesh://library/K-ID@CONTENT_VERSION/FILENAME
```

它由 CosMesh CLI／个人 Agent 解析；无需给操作系统注册协议。用户说“打开这份报告”时，Agent 执行：

```sh
cosmesh library open 'FIXED_REFERENCE' --json
cosmesh library open 'FIXED_REFERENCE' --launch --json
```

open 按固定版本按需取回，核对 SHA-256 和长度，生成本人本机私有缓存副本。回执 path 是当前用户实际可打开的绝对路径，Agent 可提供该本机文件链接；--launch 明确请求系统查看器打开。当前机器路径仅供本人阅读，共享继续使用 reference。再次打开复用缓存，--offline 要求原件和关联文件已缓存且摘要正确。

HTML需自包含才启动查看器。Markdown 只解析同项资料中已选择文件的相对链接，在缓存内准备依赖，不读取作者的项目目录；缺失／不支持的相对路径返回 unresolved_links。代码和数据外部入口的可达性单独标记。

## 重试、版本和私人边界

先上传选定资料并确认接受，再发布引用它的 Thread；普通资料索引记录提供知识库回链。断线保留本机续传回执，原样重试或同一 --idempotency-key 继续，避免重复建资料／记录。相同文件集合复用知识资料；更新本人维护的资料生成新版本，旧固定引用保持可读。

本流程仅发布所选资料，不顺带发布知识库中其他待提交草稿；同一 Mesh 仓库若有无关的未发布工作，先完成其原本预期的同步，避免把它们捎带推送。源码已有资料、私人 Thread 原件／Git历史、Profile、项目绑定和Session保留。首次共享副本将所选文件路径替换为固定引用，省略私人执行定位；既有共享历史保持，新增索引与概况修订补充当前入口。

选择文件本身属于共享内容范围，个人 Agent 先检查文件适合公开给同一Hub的用户。程序不从私人聊天、Profile或其他项目自动采集材料。上传者、作者、生成信息、修订与引用沿用既有追溯机制。

## 私人 Thread 发消息

首先显示 **⚠️ 尚未发送**。默认引导公开当前 Thread 并发送，预览正式文本范围后确认；公开后当前 Hub 用户均可读。thread share ID --sync 成功后重试原 request/reply；sent=true 才表示目标消息被服务器接受。--local-only 保存草稿，--offline 保留待发送内容，重试沿用原操作键。不会自动公开私人引用。

## 按需公开分支

仅当用户询问部分公开或保留私人部分时介绍。用 `cosmesh thread public-branch PRIVATE_ID --title '公开短名' --file APPROVED_TEXT --idempotency-key KEY --sync --json` 创建独立公开 Thread，再向该分支发送消息。只使用确认可公开的文字，来源关系仅保存在私人工作台；不复制历史、附件和绑定，不自动跟随私人更新。默认提示不展示此选项。
