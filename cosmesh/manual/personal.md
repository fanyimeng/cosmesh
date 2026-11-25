## 找到并继续工作

Thread 表示一项具体工作，有稳定 ID 和唯一所有者。人通过联系人视图查看。用 `thread find 名称 --json` 查本人本地与个人中继；明确核对最新情况用 `--require-fresh`。离线只查本机，如实报告已查范围。已知 ID 直接使用。`thread page ID --level status --ready` 查看进展，`thread locations ID` 查看设备、Agent、原生 Project、目录和 Session。选择实际工作地点，名称相同不能证明 Project 归属。

`thread personal ID --files` 将这项工作和已登记文件保存到本人 Hub。`thread fetch ID --workdir DIR --agent AGENT --files` 恢复相同 ID 并登记目的地。以回执分别确认记录、文件和执行位置。新建宿主会话须宿主支持和用户明确要求。DSH 可选组件独立，用 component status dsh 查看各 profile。

## 工作中收集

用户指定项目目录后，用 `collect add ID DIR --personal` 登记自动收集范围。多目录用不同的 `--as PREFIX`。只需本机检查点时省略 --personal。每台设备可运行一个 `collect watch` 周期收集；`collect once` 立即检查所有已登记范围。不要为每次工具调用触发 Skill。收集器记录可见文件变化；会话的重要决定、产物、待办、下一步由宿主或 Agent 在自然停顿处用 `collect summary ID --event STABLE_EVENT_ID --body TEXT` 报告。没有宿主授权的上下文不能声称已读取。停止跟踪用 collect remove ID DIR。

## 给已授权收件人更新工作

首次共享并成功发送后，发起端会持续同步这项工作。将重要决定和下一步写成 Thread 记录或 collect summary。连续写入可合并，已确认且无变化的文件可复用。如果直接在编辑器修改已登记的文件，可运行 `collect watch --interval 60` 定期检查，或运行 `collect once --json` 立即检查并上传。关闭电脑期间保留上次成功同步的版本。用 `collect status --json` 检查待同步和错误并在恢复联网后补同步。只对本人明确登记的范围运行收集。

同一份进展供所有已授权收件人使用。已有共同版本时，小改动传输变动的元数据，Hub 按校验值重建完整版本；首次同步或改动较大时走分块传输。更换设备后，先用本人的账号恢复并同步该 Thread。对外共享一个文件最多 2 GB，每批新文件最多 5 GB，每项工作的保留总量最多 15 GB。
