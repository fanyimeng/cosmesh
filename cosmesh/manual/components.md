# 可选宿主组件

CosMesh 统一管理组件的安装、版本与回退。Thread、权限和个人同步由核心负责，DSH Connector 负责原生项目、会话与初始化接续。组件独立版本化，按需安装；普通 CLI 和 Hub 不依赖 DSH 或 Node。

## 查看与安装

```sh
cosmesh component list --json
cosmesh component status dsh --json
cosmesh component install dsh --dry-run --json
```

list 读取随 CLI 提供的兼容清单；status 读取本机安装和恢复记录。两者不联网、不读取 Thread、不调用模型。dry-run 检查已知版本与宿主兼容性，不下载或创建目录，规划依据本机已有清单。

先让初始化轮结束，再退出目标 DSH 宿主，然后执行：

```sh
cosmesh component install dsh --host-stopped --json
```

status 默认列出所有已初始化的 profile，每项显示磁盘版本和适用的 next_command。可用 --profile web 单查。install、update、rollback 默认选择 desktop，其他 profile 请显式指定。自动查找本机官方 DSH CLI；非标准安装用 --dsh-cli 指定绝对路径。组件管理只调用官方 CLI，通过它维护 profile；不改写宿主配置文件、认证信息或数据库。

有网络的显式 install/update 从已配置的软件发布源读取 components/catalog.json 和选定版本 ZIP。它沿用 client.json 的 ssh/source，与个人默认 Hub 分开。新版本可通过该清单独立发布；已有版本的摘要及兼容条件不可替换。软件源尚无清单或不可达时明确失败。

离线使用已核验的本地固定包：

```sh
cosmesh component install dsh --artifact /absolute/path/to/bundle.zip --offline --host-stopped --json
```

--artifact 也接受包含 bundle.zip 的目录。版本必须在已知清单内，ZIP 摘要必须匹配；路径、资源、包身份和生命周期脚本均检查。旧的外部安装只有在其版本可校验、内容匹配时才接管；外部目录原样保留。接管旧版本前需能取得对应固定包，可先用该版本的 --artifact 安装以建立本机缓存。

## 更新与回退

```sh
cosmesh component update dsh --host-stopped --json
cosmesh component rollback dsh --offline --host-stopped --json
```

update 使用软件源当前推荐且兼容的版本；--version 可选择清单中的固定版本。每版单独保存，原版目录不覆盖。profile 配置在操作前备份，上一版保留。回退通过 DSH 官方机制重新选择旧包；首次安装回退会撤销该组件注册，保留下载包和已有资料。

已知旧版存在缺陷时，回退只作恢复手段，继续遵守对应版本的资料公开限制。旧原型与连接器工具名相同，检测到旧原型注册时停止，先通过 DSH 完成旧原型迁移。

## 安装状态与失败恢复

回执明确给出 installed、previous、backup 和 activation。官方 CLI 成功仅表示已安装，activation 为 restart_required；启动 DSH 后，通过宿主插件页核对新版运行。status 的 activation 为 unknown，避免把磁盘版本误报为运行实例版本。组件管理不会自动启动模型、创建项目或会话，也不代退出正在工作的宿主。

失败时保留 pending 记录和原包。原命令重试会核对实际安装；已完成宿主写入时只补齐回执。也可 rollback 恢复操作前状态。期间被外部工具改动的安装会停止并报告差异，保留证据供核对。不要删除 pending、改 key 或手工覆盖 profile 来绕过失败。

组件缓存默认位于托管安装前缀的 components/；非托管客户端使用 ~/.local/share/cosmesh/components。--component-root 可指定隔离的软件目录，--profile 与 DSH_HOME 标识宿主实例。软件版本回退保留该目录；管理缓存不属于 Thread 资料，也不会随 Thread 上传。
