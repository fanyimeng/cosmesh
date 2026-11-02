# 可选实验：服务器 Mesh Agent

0.2.1 的默认协作由各人的 Agent 按需完成，详见 guide show threads 的“由个人 Agent 协调”。服务器公共协调器保留为显式启用的实验组件。普通安装和预览准备不初始化、不启动它，核心协作无需模型接口或公共队列。此前已运行的实例由维护者明确停止，安装升级本身不擅自终止用户自建服务。

mesh-agent status 的 ready 仅表示状态目录可用，后台进程是否运行由 tmux 生命周期工具检查；ready 不代表服务正在运行。下面保留实验组件的接入和恢复说明。

## 功能和边界

Mesh Agent 是可选共享协调器。它把已接受的共享 Thread 整理成带来源的模型上下文，通过持久 SQLite 队列领取、重试和恢复。它不调用科研工具，不自动代表请求接收人承接工作，不读取私人 Session 或 Profile。

模型未配置时 ready=true、state=waiting_for_model，事件保留在队列，尝试次数不增长。配置模型后，默认输出保存为 review 候选；configure --publish 明确开启自动追加并同步。生成记录带 agent=mesh-agent，扫描排除这些记录，避免自己触发自己。

## 准备专用工作台

使用受控身份和独立目录运行 thread init、thread discover。共享服务可以用 --service 指定专用发现文件，沿用已有账号。用 mesh-agent init --root AGENT_STATE --workbench WORKBENCH 初始化状态。建议队列位于权威执行节点的本地文件系统，数据目录权限 700，配置和队列 600。

mesh-agent status --root AGENT_STATE 查看 ready 和模型状态，mesh-agent once 做一轮扫描/处理，mesh-agent jobs 查看队列和模型输出。--refresh 明确先拉取共享来源。mesh-agent serve --interval 30 --refresh 持续运行；--cycles 1 可验证一次启动。

## 接入模型

提供兼容 Chat Completions 的完整 HTTP(S) URL，例如 http://127.0.0.1:8000/v1/chat/completions，以及实际模型名：

mesh-agent configure --root AGENT_STATE --endpoint URL --model MODEL

配置只保存地址、模型和密钥环境变量名称，默认 COSMESH_MODEL_API_KEY。密钥值只在服务进程环境中提供，不通过配置、命令参数值、业务记录或日志保存。无认证的内网推理服务可以不设置密钥。

请求体为 model、messages、stream=false；读取 choices[0].message.content。模型需返回 title、body、source_ids 三个字段的 JSON。source_ids 必须来自请求上下文。未知来源、错误结构、超大响应或重定向都返回明确错误，保留原工作。接口依据 [Chat Completions 格式](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。本地模型、第三方兼容服务和 OpenAI 的具体模型兼容性仍需实际接入验收。

接入后运行 once，jobs 中可以看 review 输出。决定允许自动写入共享 Thread 时，重新 configure 并加 --publish。后续新事件会走生成、校验、保存和同步流程。已生成的 review 记录保留供检查。

## 恢复与运行

任务按 Thread 和来源摘要去重，工作变化产生新任务，待处理的旧快照标为 superseded。领取带租约，过期可以恢复；默认最多尝试三次。failed/publishing 任务可以 mesh-agent retry ID 后重试。发布前保存模型输出，网络重试复用原结果和稳定操作键。来源已前进时停止旧摘要发布，下一次扫描处理新快照。

tools/mesh_agent_control.py 支持普通用户用 tmux start/status/stop，服务日志保存在状态目录。serve 发生可识别错误时保留队列并输出 degraded 状态；没有配置外部通知。
