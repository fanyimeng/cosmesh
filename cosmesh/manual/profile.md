# 个人资料与问答

## 两个模块共用的私人档案

CosBase / CosMesh 读取同一执行环境的 ~/.config/cosmesh/profile.md。遵循 XDG_CONFIG_HOME，COSMESH_PROFILE_DIR 可指定隔离目录。字段包括 display_name、organization、contact、language、timezone、about、preferences，均可留空。about 是 Markdown 正文，其他字段为 YAML。

Profile 不要求先创建业务空间；--root 不切换资料。执行系统账号由内核和系统账号库取得，显示名、机构与偏好不会改变身份或授权。Mac 与服务器当前各自保存，同一账号共享 home 的两个节点共用文件，跨设备 Profile 同步仍待实现。

## 在 Agent 中问答填写

说“帮我完善个人资料”“设置我的偏好”“接着填上次的资料”，或当前任务缺少必要资料时，Agent 先读已有内容，再围绕一个主题简短询问。可跳过、暂停、继续，也可一次提供多项。

用户明确回答即可保存对应字段，无需重复确认。只更新本轮回答，未回答部分保留，清除已有内容须有明确意图。姓名、机构、联系方式与背景依据用户提供的信息。about/preferences 可以整理，保持原意。可选资料不打断当前任务。

## 命令与隐私

profile show 返回 version；缺失时为 missing。profile init 只补建空档案。profile set --expect VERSION 更新明确传入字段，简介用 --about 或 --file，空字符串清除字段，--dry-run 预览。并发冲突先保留用户回答再重新读取，同字段新内容由用户决定。

profile export --field FIELD 可多次选择字段，生成本地选择结果，published=false；对外使用仍依当前授权。普通业务 context、sync、系统反馈均不自动附 Profile。回执 sync=null，避免把私人配置误说成 Hub 已核验来源。

目录 700，文件/锁 600，检查所有者、路径和文件类型；64 KiB 总限额、安全 YAML、内容摘要、lockf 与原子替换保护读写。程序升级、回滚、卸载保留档案。个人时区作为新空间的默认值，已有空间的时区和历史不变。

## 可选的 contact 字段加密

0.1.6 起，联系方式可选加密，默认保持现有存储方式。show 返回 encrypted_fields；普通 show/export 只给出存储中的密文，其他字段继续可读。启用只作用于 contact，不改变身份、空间和同步规则。命令由 cosmesh/cosbase 任一入口调用。

```sh
cosbase profile show --json
cosbase profile encrypt --field contact --expect VERSION --json
cosmesh profile reveal --field contact --json
cosbase profile decrypt --field contact --expect VERSION --json
```

encrypt 加密保存已有联系方式；reveal 只临时返回 contact 明文，不修改档案，回执 persisted=false、published=false；decrypt 明确解除加密并保存明文。写入前使用最新 version；--dry-run 可预览 encrypt/decrypt。错误口令、密文损坏、版本冲突和写入失败保留当前档案。

第一次保存即可加密：profile set --expect VERSION --contact-stdin --encrypt-contact，contact 从标准输入读入。已加密字段的非空更新需要原口令，校验成功后继续加密，避免无意降为明文。只更新其他字段无需口令；明确将 contact 设为空字符串会清除该字段。

## 口令输入与加密格式

交互命令在终端隐藏输入口令；不从 Profile、环境变量或配置自动读取，也没有口令字符串参数。Agent/自动化可用 --key-fd N，从继承的管道或 socket 读取一行 UTF-8 口令（最多 4096 字节）；拒绝普通文件。口令只在当前进程内使用，不写档案、文档、Skill、日志或临时文件。contact-stdin 和 key-fd 0 不能共用同一输入。

Python 自动调用可以用 os.pipe 创建匿名管道，以 subprocess.run(pass_fds=(reader,)) 传递 reader；写端写入口令及换行后关闭。新联系方式同时用 subprocess 的 input 传入标准输入。需要终端隐藏输入时用 getpass；口令不要写成 Python 脚本常量。发布入口保留继承的描述符，跨 Agent 使用同一 CLI 契约。

存储形如 ENC[cosmesh:v1:contact:SALT:TOKEN]。v1 固定使用 scrypt（N=32768,r=8,p=1,32 字节输出）派生 Fernet 密钥，每次随机生成 16 字节盐；盐和密文可保存，口令和派生密钥不保存。加密载荷包含字段名并验证完整性。此前独立临时实验的格式另行标识，不自动迁入正式档案。算法依据：[Fernet](https://cryptography.io/en/latest/fernet/) 与 [scrypt](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#scrypt)。

口令丢失后无法恢复；自行保管口令。reveal 的明文交给 Agent 后会进入其上下文。已有明文备份和聊天记录继续按其原存储方式保存，字段加密只保护当前档案中的 contact。旧版客户端可保留密文，但缺少加密操作和更新保护；回滚旧版后先升级再修改加密字段。跨设备密钥管理、钥匙串保存及其他字段加密留待后续实现。

## CV / 简介导入与终端问答

profile import --file CV --dry-run 复用本地资料提取器，支持 MD/TXT/PDF/DOCX，始终只预览。带标签字段由程序提议，自由文本由个人 Agent 理解，可传 --fields-file JSON 再预览 diff；本人确认后用现有 profile set --expect 保存。未确认不写入，原件保持本地；扫描 PDF 如实标记提取缺口。

profile init --interactive 可问答填写，回车保留已有字段，/clear 明确清空，预览后输入 yes 或 确认才保存。可用 --dry-run 只预览。Mesh 实名另用 user me / set-name，在本人确认公开用途后登记。完整流程见 guide show onboarding。
