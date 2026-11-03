# 系统维护与用户意见

## 一个维护 Thread

CosMesh / CosBase 的建议、故障和使用障碍进入同一个共享维护 Thread。每条意见是普通 request，保留真实作者、接收人、原文和版本。个人 Agent 先查已有请求，同一问题通过 reply 补充；普通使用问答先查 guide。用户明确要求提交时再发送，正文只包含适合组内公开的必要信息。

thread maintenance --json 返回本地缓存的维护入口；--require-fresh 只拉取更新。首次接入用 thread discover，沿用已有连接。入口未登记或来源不可用时明确报错，不创建另一套反馈空间或自动改投旧仓库。

## 提交、回应和交付

thread request --maintenance --body TEXT --idempotency-key KEY --sync --json 向维护 Thread 创建者提出请求，也可用 --file 提供正文。普通 thread request THREAD_ID 默认向该 Thread 创建者请求，--to REAL_NAME 可明确指定接收人。--ref ID@VERSION 可重复附相关公开来源；私人 Base 引用须先按授权分享，程序不会自动公开。

维护者用 thread inbox 和 thread context REQUEST_ID 查需求，thread reply REQUEST_ID --body TEXT --sync 回应。小问题用 thread assign REQUEST_ID --work MAINTENANCE_THREAD_ID --sync 在原处承接；大改动可分配到已共享的普通开发 Thread。

thread result THREAD_ID --body TEXT --sync 保存解决结果；thread deliver REQUEST_ID --result RESULT_ID --body TEXT --sync 交付固定结果引用。请求人验证后用 thread adopt REQUEST_ID --body TEXT --sync 确认采用；拒绝、取消、部分交付沿用 decline、cancel、deliver --partial。承接、交付和采用分别留记录，个人 Agent 不替别人表态。程序不自动提醒或调用公共模型。

## 本地与共享边界

不加 --sync 只存普通 Thread 本地草稿，显式 thread sync 才发布。每件操作使用稳定幂等键，参数相同的重试复用原 ID。只有远端接受回执确认后称已共享；离线读取注明截至版本。

Mesh 内容及历史对同一 Hub 已核验用户公开。只提交必要复现描述与已核验版本，不自动附完整聊天、私人 Profile、Session/目录绑定、科研正文、凭证或日志全文。当前工作无需为反馈另外分享。

## 维护者登记

维护者先建立并分享普通 Thread，在 Hub 所在主机运行 hub maintenance --hub-root HUB --repo REPO --thread THREAD_ID 登记入口。命令仅限 Hub 所有者；目标须为已接受、处于 active 收纳状态的共享 Thread。hub maintenance --hub-root HUB 查看；--clear 取消登记并保留全部原记录。用户下次 discover 或 sync --pull 后取得入口。未配置这一可选入口不影响日常工作。

## 旧反馈兼容

feedback 命令仅保留处理旧系统的历史和原待发送项：feedback list/show 查询，--require-fresh 只拉取；feedback sync 重试原队列，feedback reply 可继续旧讨论。原 connection、repo、outbox 和回执原位保留，不自动迁移、删除或转投新入口。feedback add 作为旧客户端兼容命令保留，新意见使用上述维护 Thread。
