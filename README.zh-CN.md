# CosMesh 0.7.0

每项工作用一个 Thread 保存记录和文件。从这个 Thread 发消息时，可让指定收件人持续只读查看整项工作的最新进展，也可只发送本次消息。收件人可回到发送时的版本。

## 安装与升级

macOS 与 Linux 用户沿用维护者提供的安装命令。已有稳定渠道安装可用 `cosmesh client update` 升级。使用对应平台的离线包时，解压后运行 `python3 install.py install --skills cosmesh`；升级已有安装可在新包目录运行 `python3 install.py upgrade`，自定义安装沿用原来的 `--prefix`。

Windows 客户端适用于 Windows 11 x64、64 位 CPython 3.12 和 Git for Windows，连接 Hub 需要 OpenSSH。取得离线包时，可将下面的 HUB 替换为本人的 SSH 别名，示例目录替换为维护者提供的实际发布目录。在 PowerShell 中执行以下命令。

```powershell
scp HUB:/path/to/releases/cosmesh-0.7.0-win32-AMD64-py312.zip .
Expand-Archive .\cosmesh-0.7.0-win32-AMD64-py312.zip -DestinationPath .
Set-Location .\cosmesh-0.7.0-win32-AMD64-py312
$prefix = Join-Path $env:LOCALAPPDATA 'CosMesh'
py -3.12 -I -X utf8 .\install.py install --prefix "$prefix" --skills cosmesh
& "$prefix\bin\cosmesh.cmd" guide show upgrade
```

以后解压新包，可用已安装的 `cosmesh.cmd client update --release 新包目录` 升级。已经试装过同版号 0.7.0 预览包的用户，请为正式程序选择独立前缀；要继续使用原资料，沿用原来的数据目录或 `--root`。Windows 原生 NTFS 权限及 OpenSSH 双账号联测仍待实机确认。

首次安装和升级后会显示简明说明；随时用 `cosmesh guide show upgrade` 重看。下列示例中的 cosmesh 也可换成上面的 Windows 命令入口。

## 常用命令

```sh
cosmesh thread find 项目名
cosmesh thread continue THREAD_ID --ready
cosmesh thread note THREAD_ID --body "完成了第一轮检查"
cosmesh thread send THREAD_ID --to ACCOUNT --body "请看最新进展"
cosmesh thread inbox
cosmesh thread mail open MESSAGE_ID --records
cosmesh thread mail open MESSAGE_ID --at-send --files
cosmesh collect status
```

[快速上手](cosmesh/manual/upgrade.md) · [工作区共享和撤权](cosmesh/manual/sharing.md) · [托管 Skill](cosmesh/skills/cosmesh/SKILL.md) · [可选组件](components/README.md)
