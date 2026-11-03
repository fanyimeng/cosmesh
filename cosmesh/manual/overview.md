# CosMesh / CosBase 系统说明

## 定位、入口与版本

本说明随程序固定版本发布，适合用户自己的 Agent 检索并作为交互式指导材料。guide list 列出主题，guide search 检索段落，guide show 读取全文或指定 section。回执包含程序版本、资源名、内容摘要和行号。不同版本的行为先以当前 capabilities 和命令帮助核对。

默认采用本地两份 Skill + 本地 CLI。安装器自动准备合适的运行环境，用户无需选择电脑架构或 Python 包。Base 独立离线使用，Mesh 在线缓存后可离线读写；显式 sync、require-fresh 和更新才联系服务器。

CosMesh 保存共享上下文、行动与记录；CosBase 整理个人事务。两者共用文档、存储、身份接入、Profile、说明和系统反馈能力，可以分别使用。科研是应用场景之一，团队事务、活动组织和个人计划采用同样的通用语义。

日常入口为用户自己的 AI Agent + 标准 Skill + CLI/JSON。beta 面向 Codex 和 OpenCode。用户用自然语言描述需求，Agent 查说明、读上下文、执行授权操作并核对结果。程序负责确定性的校验、持久化与同步，内容理解和问答由用户的 Agent 完成。

## Thread 与兼容文档

新工作使用 Thread，保存概况、追加记录、请求、结果及固定来源。知识库独立保管资料版本，通过回链找到引用过它的工作。Thread 工作流见 threads 主题，检索见 search 主题。以下三种文档说明用于兼容旧工作空间。

### 旧版三种文档与派生视图

Project 保存目标、背景和范围；Task 表达需要推进的行动、责任与日期；Update 保存进展、想法、决定、反馈、更正与交接。源文件是 Markdown 正文和少量 YAML 元数据。文档有稳定 UUIDv4 ID、内容版本和 Git 历史。

Inbox、Agenda、context、任务当前状态是派生视图。Session 是 Update 的定位元数据。Profile 是私人用户配置；旧系统反馈历史同样沿用原 Project 和 feedback 类型 Update。

## 当前已有能力

CosBase 支持独立记录、默认个人项目、全部六向文档转换、派生新建、归档、回收、恢复、结束与重开、等待与日期查询。

CosMesh 支持项目和任务、初始及追加邀请、本人接受或拒绝、进展与反馈、更正与回复、Session、Lead 控制的状态和日期。共享 Update 被 Hub 接受后保持不变，更正与回复追加新记录。

共用能力包括稳定 JSON、版本前提、幂等、仓库锁、写入恢复、固定 Git 快照、显式同步、既有系统身份、私人 Profile 与问答填写、离线系统说明、维护 Thread 中的普通请求与回应。新意见使用 thread request --maintenance，原专用反馈仅保留兼容。

Profile 联系方式可选择字段加密；普通读取保留密文，按需 reveal 临时获取选定明文，明确 decrypt 可解除加密。口令通过隐藏终端或临时管道输入，不写档案和文档。使用方式见 profile 主题。

## 当前边界

日期与能力按各命令契约解释。TUI 是独立内存演示。Thread 可明确分享，知识库可保存文档原件并提取文字。私人 Profile 跨设备同步、手机入口、提醒、后台自动巡检和 MCP 仍在规划中。按 capabilities 与相应 guide 主题判断当前可用性。

新 Agent 会话的自动发现、自然语言触发与问答体验需要在实际宿主验收；Skill 文件安装与 CLI 测试分别记录。程序不内置 LLM，也不要求另配模型 API key。

## 私人与公开边界

Base 私有，Mesh 对所有已登录 CosMesh 用户公开读取。每个服务器账号首次接入自动初始化自己的 Mesh 空间，安装/更新返回公开空间列表。space discover / list / fetch USER 完成接入、发现与按需抓取，无需空间邀请或管理员提供仓库名。作者、Lead、项目成员和 Task 接受规则继续约束写入。Profile 保持私人。
