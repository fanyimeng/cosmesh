# 分级状态页

状态页由现有 Thread、记录、实名目录与本人执行登记生成；原资料及历史保持原位。默认本地读取，成品与索引可重建，无模型、后台联网或自动分享。

## 日常读取

默认快速模式开启，直接复用成品，减少额外读取与转述；client mode normal 可关闭。默认成品读取使用 --ready：

```sh
cosbase thread page --ready --json
cosbase thread page --continue THREAD_ID --ready --json
cosbase thread page THREAD_ID --level status --ready --json
```

status.page.ready.v1 仅含 document 成品正文、去重的 source_index 与已知作者、rows/focus 来源下标及新鲜度/分页标记。可直接呈现 document，避免再发送同一套结构化卡片或让 Agent 重排总览。优先显示已保存中英文短名与一句简介，未登记时保留整体介绍；进展、范围、私人项目关联及待更新标记保留。默认一张表，显示 Agent 项目／工作区的关联情况；Session 和目录按需展开。需要完整字段时省略 --ready；原接口保持。--ready 和 --markdown 分开使用；省略 --json 时直接打印成品。

同一对话已经读到且没有新变化的材料可直接复用，注明截至时间；用户要求最新、材料变化或实际执行时，定点核验对应来源。成功写入使用回执回答，不固定再读一次。

## 请求与资料短入口

```sh
cosmesh thread check REQUEST_ID_OR_THREAD_ID --json
cosmesh thread check REQUEST_ID --require-fresh --json
cosmesh thread inbox --ready --json
cosmesh thread inbox --sent --ready --require-fresh --json
```

check 保留工作整体介绍、当前状态、接收人的最近回应及资料登记，个人 Agent 先用两三句话概括工作、进展和待办。默认只读已有成品；问“有没有新回应／现在怎么样”时同次 --require-fresh，只拉取一次。已有回应与正式 pending 可以同时成立，reply 不改变交付状态。请求较多时默认显示最近五项，--limit 1–100 可调整；已知请求 ID 可直接定位旧请求。

资料状态仅来自固定版本的知识库元数据及作者的资料索引，保留缺项与登记来源，verification=metadata_only；默认不下载原件、校验字节、渲染报告或展开引用全文。真正要求打开或核验时，直接用回执里的固定引用 library open。缓存命中不加载工作台；来源变化后沿用现有局部更新，不新增后台服务。

inbox --ready 给出有界请求清单、正式状态与 has_response，默认最多十项，可 --limit 调整。普通 inbox 完整契约保持。create、note/result/summary、request/reply、assign/deliver/adopt 等写入可 --ready 保留 ID、固定版本、必要引用、资料及同步错误，省略重复正文与逐仓库同步明细；成功回执足够就结束。原接口省略 --ready 仍给完整结果。

```sh
cosbase thread page --json
cosbase thread page --continue THREAD_ID_OR_SAVED_NAME --json
cosbase thread page THREAD_ID --level status --json
cosbase thread page THREAD_ID --level index --limit 20 --offset 0 --json
cosbase thread page --all --query TEXT --json
cosmesh thread page --scope mesh --user REAL_NAME --json
cosbase thread page THREAD_ID --level status --markdown
```

不指定目标返回本人用户页，默认活动工作；--all 纳入历史。三层为 brief/status/index，--limit 1–100 与 --offset 有界分页。--query 在用户卡片或目标细节中作文字筛选，不宣称语义检索；需要模糊查找时继续用已有 search 工作流。

brief 每条卡片都保留 overall，说明整体工作、目标与范围，和 progress 分开。整体介绍来自工作定义或创建者登记的有效“整体介绍”归纳；不足时 missing，长文截断时 truncated，来源固定。程序不把最近进展当作整体介绍，也不补写未经依据的结论。

status 包含目标、已登记归纳、成果、更正、请求及细节入口。index 保留所有记录的分页索引、作者、ID@VERSION、引用和更正去向，旧记录继续可定位。JSON 直接给出结构化成品，避免再附同一 Markdown 正文；--markdown 取得可读正文，省略 --json 时直接打印 Markdown。

## 更新与新鲜度

SQLite 索引和 MD 位于当前机器临时目录下的本人 700 缓存，文件 600，与工作台、安装、共享 home 分离。COSMESH_PAGE_CACHE 可指定独立的本人本地缓存目录。缓存是派生产物，既有记录与贡献仍是依据。

首次读或来源版本变化尚未处理时，前台生成成品；有效命中只核对本地已提交来源版本、绑定和目录快照变动，不解析全工作台。cache.hit、generated_at、source_token、local_sources_current、remote_checked_at 分别表达缓存与远端核验状态。单次读取开始时核对版本，并发更新可继续前进；实际执行前仍核验有关控制状态。

成品建立后，CLI 写入、共享拉取及知识目录更新会触发短后台程序，合并连续变更，按内容与依赖摘要只替换受影响 Thread 及其用户页。无常驻轮询或模型调用，每次后台处理有 45 秒上限；失败保留队列，源操作成功回执不受影响。下一次读取或 --refresh 可完成本地恢复。COSMESH_PAGE_BACKGROUND=0 可停用自动后台准备，读取时仍按需更新。

--require-fresh 先拉取一次共享来源；Base 视图保持本地。未联网时只能说截至本地版本，不能称服务器最新。状态改变直接重建事实；summary.pending 表示旧归纳之后有新材料，只作历史参考，旧的下一步不作为当前建议。

MD 文件按页完整替换，SQLite 事务提供一致的查询版本。独立 MD 是可读副本，跨多份文件的读取需按页面版本核对；正式 CLI 从统一索引读取。缓存损坏保全坏副本后重建；源格式和 Git 历史不迁移。

## 个人 Agent 维护归纳

```sh
cosbase thread summary THREAD_ID --file SUMMARY_MD --ref ID@VERSION --generation-file GENERATION_JSON --json
```

复用既有 summary 源记录，仅创建者维护，沿用 --idempotency-key 和固定引用。建议用“## 整体介绍”“## 下一步”组织正文，工作整体介绍写两三句话，进展另写；generation 沿用 tool/model/instruction/verification 字段。旧归纳及原作者保留。其他人用自己的 note/result 提供贡献。

普通只读查询不写归纳；个人 Agent 在已有记录授权的工作流程中整理。程序负责事实、摘录、版本和索引，语义理解由个人 Agent 完成。

## 私人边界与细节核验

all/base 为本人视图，Mesh 只取共享来源，不含私人工作、执行登记、私人回链或本机缓存路径。其他用户页限定 --scope mesh，实名歧义返回候选。共享成功和失败回执均遵守此边界。

状态页给出概况与细节定位。需要精确结论、固定证据或实际执行时，用已有 search read/context/continue 沿记录与回链读取原文、更正和适用条件。会话/目录仅登记，核验或恢复仍按实际需要执行。缓存失效不改变请求接受、结果采用或生命周期状态。
