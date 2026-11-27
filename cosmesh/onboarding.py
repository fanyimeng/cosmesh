"""Offline, shareable installation instructions from public connection fields."""
import re
import shlex
from pathlib import PurePosixPath
import json

from . import __version__
from .errors import Error
from .skills import AGENTS


def absolute_server_path(value):
    if (not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or '..' in PurePosixPath(value).parts):
        raise Error('INPUT_INVALID', 'Use an absolute server path containing letters, numbers, /, _, . or -.')
    return value


def server_entry(root=None, source=None, service=None):
    from .spaces import settings
    from .threads import workspace_root
    saved = settings()
    connection = {}
    path = workspace_root(root) / '.cosmesh/thread-client.json'
    if path.is_file():
        connection = json.loads(path.read_text(encoding="utf-8")).get('connection') or {}
    discovery = service or saved.get('discovery') or connection.get('discovery')
    source = source or (str(PurePosixPath(service).parent / 'agent') if service else saved.get('source'))
    source = source or (str(PurePosixPath(discovery).parent / 'agent') if discovery else None)
    if not source:
        raise Error('HUB_CONNECTION_REQUIRED', 'Choose GitHub mode, or provide the public Hub --source path.')
    source = absolute_server_path(source)
    return {'source': source, 'service': absolute_server_path(service or discovery or str(PurePosixPath(source).parent / 'service.json'))}


def installation_prompt(mode, agent, *, root=None, source=None, service=None, ssh=None, version=None):
    version = version or __version__
    if mode not in ('server', 'github') or agent not in AGENTS or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise Error('INPUT_INVALID', 'Select server/github, a supported Agent and a release version such as 0.3.15.')
    if mode == 'github' and any((source, service, ssh)):
        raise Error('INPUT_INVALID', 'GitHub mode uses public source only; omit server options.')
    if ssh and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', ssh):
        raise Error('INPUT_INVALID', 'Use the recipient\'s existing SSH alias.')
    intro = f'''请帮我在这台设备上安装 CosMesh 和托管 Skill。已有安装请按原渠道升级。此提示词由 {__version__} 生成，目标版本 {version}，Agent 为 {agent}。
先检查本机平台、Git、Python 以及已有安装的实际前缀和更新渠道。已有安装保留原位置、工作数据和自定义 Skill。
'''
    if mode == 'server':
        endpoint = server_entry(root, source, service)
        # An explicit new source implies its own service unless separately specified.
        if source and not service:
            endpoint['service'] = str(PurePosixPath(endpoint['source']).parent / 'service.json')
        alias = ssh or 'YOUR_SSH_ALIAS'
        command = shlex.join(['sh', 'COSMESH_INSTALLER', alias, '--source', endpoint['source'], '--service', endpoint['service']])
        prompt = intro + f'''
我已有此 Hub 的服务器账号。准备信息：我自己的 SSH 别名 {alias}；如果仍是占位符，请先问我。使用该别名原有的登录身份。
公开软件入口：{endpoint['source']}/install.sh
公开服务发现：{endpoint['service']}

根据当前设备选择一种安装方式。源目录中的 INSTALL.md 提供完整步骤。
- Windows 11 x64：从 {endpoint['source']}/stable-windows.json 读取 archive 和 archive_sha256，用本人的 SSH 别名取得 ZIP 并核验。需要 64 位 CPython 3.12、Git for Windows 和 OpenSSH。在解压目录按 INSTALL.md 的 PowerShell 步骤安装；已有安装用原 cosmesh.cmd client update --release 解压目录。
- 直接在 Hub 的 Linux 服务器运行：用本人的登录账号执行 python3 {endpoint['source']}/bootstrap.py ensure。通过本机服务文件连接。
- macOS / Linux 电脑：先确认 Git 和 SSH 连接。从上述软件入口用 scp 取得 install.sh 到新的临时目录，用实际文件路径替换 COSMESH_INSTALLER 并运行：
  {command}
  安装器选择 Python 3.9–3.13，必要时准备专用 Python。已有稳定渠道的个人客户端使用其原入口运行 client update --json。

1. 用安装器回执给出的绝对 CLI 路径执行 skills install --agent {agent} --only cosmesh --json，再 doctor --agent {agent} --json。用户修改过的 Skill 保留并按实际冲突处理。
2. 从个人电脑执行 thread discover --ssh {alias} --service {endpoint['service']} --json；在服务器本机执行 thread discover --service {endpoint['service']} --json。用 user me --require-fresh --json 核对身份；缺少姓名时问本人，说明实名用于组内展示，再 user set-name。私人 Profile 可选，可在安装后导入简介或交互填写。
3. 执行 thread status --json，确认身份、空间和同步状态；新 Thread 默认私人，明确分享才公开。
4. 使用实际安装版本的 guide，报告安装回执与实际版本。兼容回退用 self rollback，具体参数查 --help。
遇到身份、权限、INSTALL_IO_ERROR 时先查 guide show operations / dsh，按本人授权解决后重试。DSH 跨目录使用推荐完全访问，由本人选择。最后报告实际 CLI 路径、版本、统一的 Skill、实名状态和 Mesh 状态；未成功的步骤明确列出。
'''
    else:
        endpoint = None
        prompt = intro + f'''
我还没有服务器账号，先只用私人 Thread。公开源码：https://github.com/fanyimeng/cosmesh ，固定 tag：v{version}。
如已有与本平台、Python 版本匹配的官方 ZIP，核对其校验值，用解压目录的 INSTALL.md 和安装器；Windows 11 x64 使用 64 位 CPython 3.12、Git for Windows 和包内 PowerShell 说明。没有匹配附件时从以下固定 tag 构建。

1. 检查 Git、Python 3.9–3.13、venv/pip 和联网能力；缺少时先准备专用运行环境。取得 v{version} 到新的源码目录；已有目录先核验并保留。tag 不存在时停止，说明尚未发布，不换用不明版本。
2. 在专用构建虚拟环境先执行 python -m pip install --upgrade pip 'setuptools>=61' wheel，避免系统附带的旧构建工具把项目识别为 UNKNOWN。按 requirements-release.txt 安装固定依赖，再安装该源码，运行 python tools/build_release.py --output dist/candidates。根据构建回执取得本机匹配的发布包路径。
3. 从该固定包运行 python3 install.py install --agent {agent} --skills cosmesh；已有托管安装按实际版本使用 upgrade，保留其 prefix 和托管 Skill 目录。使用匹配该包的 Python。
4. 使用安装回执给出的绝对 CLI 路径，执行 doctor --agent {agent} --json、thread init --json、thread list --mine --all --json。询问我第一件要记录的工作，复用已有事项，确认后再建立私人 Thread。私人 Profile 可选，可导入简介或交互填写。
5. 这条路径不设置服务器连接。以后取得自己的账号和 SSH 别名，再 thread discover --ssh YOUR_SSH_ALIAS --service PUBLIC_SERVICE_FILE --json，user me / set-name，thread status 验证；公开服务文件位置由 Hub 维护者提供。
6. 源码安装的更新继续从新 tag 构建固定包并用新包的 install.py upgrade；client check/update 需要另行配置对应渠道。回退前查看 self status 和兼容性门槛。
遇到问题先查该版本 guide / --help。最后给出实际版本、CLI 绝对路径、Skill 检查、私人 Thread 状态和未来接入 Mesh 尚需的信息。
'''
    prompt += (
        '首次安装或跨版本升级后，向我展示 update_notice.document。启动已安装的 CLI 检查版本、'
        'Skill 和本机项目说明迁移结果。用 thread project-guidance --dry-run 核对，仍有待补项时可运行 '
        'thread project-guidance 重试，并指出需要处理的目录。一目录多 Thread 时按本次工作选择。\n'
        if tuple(map(int, version.split('.'))) >= (0, 7, 1) else ''
    )
    return {'mode': mode, 'agent': agent, 'target_version': version, 'generator_version': __version__,
            'server': endpoint, 'prompt': prompt.strip() + '\n', 'generated_locally': True, 'published': False}
