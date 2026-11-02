"""Export architecture-independent SSH Skills; business operations stay in the CLI."""
import hashlib
import os
from pathlib import Path
import re

from . import __version__
from .errors import Error
from .skills import source

RESOURCES = Path(__file__).parent / 'resources'


def export(host, bootstrap='/srv/cosmesh/agent/bootstrap.py'):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise Error('INPUT_INVALID', 'Use an existing SSH alias without options or shell syntax.')
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if not prefix or not Path(prefix).is_absolute():
        raise Error('NOT_MANAGED_INSTALL', 'Export SSH Skills from the installed server CLI.')
    if any(c in prefix + bootstrap for c in '\r\n\x00') or not Path(bootstrap).is_absolute():
        raise Error('INPUT_INVALID', 'Use absolute, single-line server paths.')
    generated = []
    for name in ('cosmesh', 'cosbase'):
        entry = f'''

## 执行入口：已有 SSH（本 Skill 的当前配置）

本地只安装 Skill 与其 shell 资源，无需本地 CosMesh CLI、Python 或 Git。SSH 别名为 `{host}`；服务器程序与数据在该 SSH 登录账号下。本 Skill 中的 `{name}` 命令均通过本 Skill 目录内 `scripts/run.sh` 调用，例如：

```sh
sh ~/.agents/skills/{name}/scripts/run.sh capabilities --json
sh ~/.agents/skills/{name}/scripts/run.sh guide search '用法' --json
```

若 Skill 实际位于其他目录，使用该 Skill 自身的路径。脚本保留参数、stdin、JSON 和退出码，通过已有 SSH 调用服务器 `{prefix}/bin/{name}`。业务规则和命令保持相同；禁止将服务器数据位置解释成本机目录。

本地正文文件用 `--file -` 配合输入重定向，例如更新简介时 `sh 本Skill/scripts/run.sh profile set --expect VERSION --file - --json < 本地简介文件`。--root 和其他业务路径属于服务器；本地文件不直接充当服务器路径。Session 继续描述实际工作发生的 Agent、host、workdir。

用户询问更新时运行 `sh 本Skill/scripts/run.sh --check-updates`；明确要求更新时运行 `sh 本Skill/scripts/run.sh --update`，先准备服务器 CLI，再更新未修改的两份本地 Skill。更新失败分别报告服务器和本地结果，不从网络失败推断写入没有发生，业务重试沿用幂等键。没有后台检查或自动更新。

需要口令的操作使用用户终端中的 `sh 本Skill/scripts/run.sh --tty profile encrypt --field contact --expect VERSION` 等；口令由远端终端隐藏输入。自动化仅在有临时内存管道时使用 --key-fd 0；同时发送新联系方式和口令时请用户在终端操作，避免把口令写入脚本、文件、参数值或聊天。服务端 reveal 的明文进入本地 Agent 上下文后按私人资料处理。

个人 Base、Profile、反馈队列与业务副本均位于服务器账号 home；需要已有 SSH 连接。已有本地资料保持原位，迁移由用户明确选择。doctor 检查服务器环境时不将其结果当作本地 Agent 的 Skill 发现验证；新会话的发现、自然语言触发和真实调用分别验收。
'''
        files = {
            'SKILL.md': (source(name) / 'SKILL.md').read_text() + entry,
            'scripts/run.sh': (RESOURCES / 'remote-run.sh').read_text(),
            'connection': host + '\n' + prefix + '\n' + bootstrap + '\n',
            'version': __version__ + '\n',
        }
        files['SHA256SUMS'] = ''.join(hashlib.sha256(text.encode()).hexdigest() + '  ' + file + '\n'
                                      for file, text in files.items())
        for file, text in files.items():
            marker = 'COSMESH_' + hashlib.sha256(text.encode()).hexdigest()
            if marker in text.splitlines():
                raise Error('INTERNAL_ERROR', 'Export delimiter collision.')
            generated.append('cat > "$cosmesh_stage/new-%s/%s" <<\'%s\'\n%s%s\n' %
                             (name, file, marker, text, marker))
    return (RESOURCES / 'remote-payload.sh').read_text().replace('# GENERATED_FILES', '\n'.join(generated))
