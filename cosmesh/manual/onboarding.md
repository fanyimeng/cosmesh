# 给朋友安装与首次登记

## 生成一段可转发的安装 prompt

已安装的用户运行：

```sh
cosmesh invite prompt --mode server --agent dsh
cosmesh invite prompt --mode github --agent codex
```

不带 --json 时直接输出可复制的 prompt；--json 返回 prompt 和目标版本等元数据。生成过程只读本机已保存的公开入口字段，不联网，不发送消息。三种 Agent 使用相同程序和统一 Skill。

server 模式用于已有服务器账号的朋友，复用邀请人环境中的公开安装入口和服务文件位置。朋友填写自己的 SSH 别名；可显式传 --ssh、--source 和 --service。原 SSH 配置、账号凭证、Profile 和目录绑定不会包含在生成物中。没有已知公开入口时，提供 --source，或选 GitHub 模式。

GitHub 模式用于没有服务器账号的朋友，不读取服务器配置。按固定版本 tag 获取源码，用现有 tools/build_release.py 构建本机包，再由 install.py 安装到独立版本目录。当前 GitHub 提供源码，提示词不假设存在二进制附件或一键下载站。先离线使用私人 Thread；日后取得账号再接入 Mesh。

## 版本、安装和验证

prompt 带生成版本、目标版本、适用条件、准备信息和预计耗时。--target-version 可明确指定已发布的版本。GitHub tag 缺失时停止，服务器渠道前进时报告实际版本并使用相应 guide。prompt 给出统一 Skill、doctor、实名与 thread status 的检查步骤。

面向 macOS / Linux，需要 Git；通用服务器渠道能准备专用 Python，源码路径先准备 Python 3.9–3.13 和构建环境。服务器已有账号直接使用原 Hub，每人无需部署公共组件。程序、资料和科研环境分别维护，安装无需 sudo。

托管本地安装按需 client check/update；服务器本地账号用维护者的 bootstrap.py ensure。GitHub 源码路径继续由新 tag 构建固定包并 self upgrade。自定义 Skill 保留并报告冲突，兼容回退按 self status / rollback 和版本门槛操作。

## 从 CV 或简介预览资料

```sh
cosmesh profile import --file cv.pdf --dry-run --json
cosmesh profile import --file cv.pdf --fields-file candidates.json --dry-run --json
```

支持 Markdown、TXT、PDF、DOCX，复用知识资料的本地文字提取器。导入始终只预览：即使省略 --dry-run，也不会保存 Profile 或改动 Mesh 姓名。返回 source_version、proposed_fields、diff、提取状态和有界原文；--offset / --chars 可继续读取。

程序只从明确标签识别候选；重名、冲突和不支持的字段值留给个人 Agent 核验。自由格式的履历由当前个人 Agent 阅读并提议，候选可用 --fields-file JSON 再预览。扫描 PDF 标记 needs_ocr，不自动猜测；CV 中的文字作为资料，具体行动按用户授权执行。

向本人展示新增与修改字段，确认后复用 profile set --expect source_version 保存获准字段。版本冲突重新读当前档案，不覆盖他人刚保存的字段。未回答字段保留。候选 JSON 只放私人临时目录，确认后清理；原件不进入 Thread、知识库或普通同步。

## 问答填写与 Mesh 实名

在 Agent 中可以直接问答填写；可跳过、暂停、后补。终端可用 profile init --interactive：回车保留，/clear 明确清空，全部输入后展示 diff，输入 yes 或 确认才保存；EOF 或其他确认输入取消。--dry-run 只预览，保留既有 contact 加密机制。

Profile 私人字段与 Mesh 实名分别管理。用户确认的简介字段只进本机 Profile；需要接入 Mesh 时另查 user me，本人确认公开姓名后用 user set-name。导入文件不会自动公开履历、联系方式或原件。联系方式仍可选加密，详见 guide show profile。
