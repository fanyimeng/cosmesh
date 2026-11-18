# 实名用户与稳定身份

Hub 通过本人服务器账号或认证 SSH 身份分配稳定 person-ID。首次共享由本人 user set-name NAME 填写真实姓名；修改已有姓名需 --expect VERSION。姓名是本人填写，系统认证的是账号归属。

user me/list/show 查询目录；账号、稳定 ID 或唯一实名均可定位，重名返回候选。改名不改变 Thread 所有权或追踪关系。Profile 的联系方式和偏好继续私人保存。

thread access ID --visibility restricted --to USER 授予查看，--to 可重复；权限成功不表示发送通知。thread browse --owner USER 在线查看当前有权读取的工作；thread track USER --use ID 或 --create 追踪指定给本人的来源，排除全员可见。

Library 与贡献视图按当前来源权限过滤。消息用 thread send/reply，从本人 Thread 发给有权查看的接收者。历史消息与查看标记保留；反馈保留固定来源引用。
