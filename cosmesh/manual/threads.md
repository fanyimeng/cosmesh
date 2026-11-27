# 执行位置

本人可以通过 thread locations ID 查设备、Agent、原生 Project、目录和会话。用 thread bind-project 登记项目，实际目录可用 --kind directory --workdir PATH 登记。目录名称相同的不同位置保留各自的路径。

缺少关联时，用 thread associate --missing 查看。thread associate ID --host-file FILE 使用宿主提供的有限项目与会话元数据预览候选；唯一可验证的候选可用 --confirm CANDIDATE --expect REVISION 确认。两个 Agent 中同名的项目各自保留原生 ID。

项目绑定记录工作位置。需要收集目录文件时使用 collect add。会话决策由宿主提交摘要，或明确选择交接文件。

绑定本机目录时，CosMesh 会在该项目的 AGENTS.md 里补充开工读 Thread、阶段进展与收尾写回的说明；项目已有流程继续适用。目录关联多个 Thread 时按当前工作选择。用 `cosmesh thread project-guidance --dry-run` 检查以前的绑定或新绑定，`cosmesh thread project-guidance` 补做。
