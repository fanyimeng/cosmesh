#!/usr/bin/env python3
"""Build an immutable, platform-specific offline user release from a wheel."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(args):
    subprocess.run([str(a) for a in args], check=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_version(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('Expected one distribution in the wheel.')
        values = Parser().parsestr(archive.read(metadata[0]).decode())
        if values['Name'] != 'cosmesh' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', values['Version']):
            raise ValueError('Expected a stable numeric CosMesh wheel version.')
        return values['Version']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--wheel', type=Path, help='Use an already built, versioned wheel, including the original 0.1.0 artifact.')
    mode.add_argument('--promote', type=Path, help='Copy a verified candidate to the release directory without rebuilding it.')
    parser.add_argument('--output', type=Path, default=ROOT/'dist/releases')
    cross = parser.add_mutually_exclusive_group()
    cross.add_argument('--windows-x64', action='store_true',
                        help='Build an offline Windows x64 bundle for CPython 3.12 from a pure-Python CosMesh wheel.')
    cross.add_argument('--linux-x64', action='store_true',
                       help='Build a Linux x86-64 / CPython 3.12 offline bundle for a test Hub (glibc 2.34+).')
    parser.add_argument('--wheelhouse', type=Path,
                        help='Resolve dependency wheels from this local directory with no index access.')
    args = parser.parse_args()
    if args.promote and (args.windows_x64 or args.linux_x64 or args.wheelhouse):
        parser.error('--promote takes an existing native candidate unchanged')
    target_platform, machine, python_version = sys.platform, platform.machine(), sys.version_info[:2]
    if args.windows_x64:
        target_platform, machine, python_version = 'win32', 'AMD64', (3, 12)
    elif args.linux_x64:
        target_platform, machine, python_version = 'linux', 'x86_64', (3, 12)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.promote:
        import release_installer
        candidate,meta=release_installer.bundle(args.promote)
        release_id=meta['release_id']
        archive=candidate.parent/(release_id+'.zip')
        target=args.output/release_id
        archive_target=args.output/(release_id+'.zip')
        if target.exists() or archive_target.exists():
            raise FileExistsError('Release artifacts are immutable: '+release_id)
        with zipfile.ZipFile(archive) as package:
            for item in candidate.rglob('*'):
                if item.is_file():
                    assert package.read(release_id+'/'+item.relative_to(candidate).as_posix())==item.read_bytes()
        with tempfile.TemporaryDirectory(prefix='.release-promotion-',dir=str(args.output)) as temp:
            stage=Path(temp)
            shutil.copytree(candidate,stage/release_id)
            shutil.copy2(archive,stage/archive.name)
            release_installer.bundle(stage/release_id)
            (stage/release_id).rename(target)
            (stage/archive.name).rename(archive_target)
        print(json.dumps({'version':meta['version'],'directory':str(target.resolve()),
                          'archive':str(archive_target.resolve()),'sha256':sha(archive_target),'rebuilt':False},ensure_ascii=False))
        return
    with tempfile.TemporaryDirectory(prefix='.release-build-', dir=str(args.output)) as tmp:
        staging = Path(tmp)
        wheels = staging/'payload/wheels'
        wheels.mkdir(parents=True)
        if args.wheel:
            wheel = args.wheel.resolve()
        else:
            build_dir = staging/'built'
            run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',build_dir,ROOT])
            wheel = next(build_dir.glob('cosmesh-*.whl'), None)
            if wheel is None:
                raise RuntimeError('Project wheel metadata is missing. In the build environment run: '
                                   'python -m pip install --upgrade pip "setuptools>=61" wheel')
        version = wheel_version(wheel)
        release_id = 'cosmesh-%s-%s-%s-py%d%d' % (version,target_platform,machine,*python_version)
        target = args.output/release_id
        archive_target = args.output/(release_id+'.zip')
        if target.exists() or archive_target.exists():
            raise FileExistsError('Release artifacts are immutable: '+release_id)
        shutil.copy2(wheel,wheels/wheel.name)
        deps = [sys.executable, '-m', 'pip', '--isolated', 'download', '--only-binary=:all:',
                '--disable-pip-version-check', '--no-deps', '--dest', wheels]
        if args.wheelhouse:
            deps += ['--no-index', '--find-links', args.wheelhouse.resolve()]
        requirements = ROOT / 'requirements-release.txt'
        if args.windows_x64 or args.linux_x64:
            if not wheel.name.endswith('-py3-none-any.whl'):
                raise ValueError('Cross-building needs a platform-independent CosMesh wheel.')
            # pip's --platform selects wheel tags; its requirement markers still
            # use the host. Select dependency pins against the declared recipient.
            from pip._vendor.packaging.requirements import Requirement
            target_environment = dict(sys_platform=target_platform, os_name='nt' if args.windows_x64 else 'posix',
                                      platform_system='Windows' if args.windows_x64 else 'Linux',
                                      platform_machine=machine, python_version='3.12',
                                      implementation_name='cpython', platform_python_implementation='CPython')
            platforms = ['win_amd64'] if args.windows_x64 else ['manylinux_2_34_x86_64', 'manylinux_2_17_x86_64', 'manylinux2014_x86_64']
            for tag in platforms:
                deps += ['--platform', tag]
            deps += ['--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312']
            for line in requirements.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                req = Requirement(line)
                if not req.marker or req.marker.evaluate(target_environment):
                    deps.append(req.name + str(req.specifier))
        else:
            deps += ['-r', requirements]
        run(deps)
        payload = staging/'payload'
        shutil.copy2(ROOT/'tools/release_installer.py',payload/'install.py')
        if target_platform != 'win32':
            agent = payload/'agent'
            agent.mkdir()
            shutil.copy2(ROOT/'tools/remote_bootstrap.py',agent/'bootstrap.py')
            for helper in ('prepare_thread_preview.py',):
                shutil.copy2(ROOT/'tools'/helper, agent/helper)
            shutil.copy2(ROOT/'cosmesh/resources/remote-install.sh',agent/'install.sh')
        instructions = '''# CosMesh 用户发布包 %s

适用环境：%s / %s / Python %d.%d，另需 Git。依赖 wheels 随包提供，安装不联网。

先解压发布包，在本目录打开终端。首次安装（可先加 --dry-run 预览）：

    python3 install.py install --skills cosmesh

默认安装到 ~/.local/opt/cosmesh，选装 Skill 位于 ~/.agents/skills。
可用 --prefix 和 --skills-dir 指定独立测试目录，--skills none 跳过 Skill。

首次安装和升级成功后会显示本版快速上手说明，可用 cosmesh guide show upgrade 重看。

查看安装：

    ~/.local/opt/cosmesh/bin/cosmesh self status
    ~/.local/opt/cosmesh/bin/cosmesh --version

首次初始化本地 Thread 数据：

    ~/.local/opt/cosmesh/bin/cosmesh thread init --json

默认使用当前系统账号。默认数据位于 ~/.local/share/cosmesh，升级和卸载保留数据。
新终端可设置 PATH：export PATH="$HOME/.local/opt/cosmesh/bin:$PATH"

下载并解压新版后，在新包目录执行：

    python3 install.py upgrade

0.3 升级至 0.4 请使用新包安装器；同代版本也可用 cosmesh self upgrade --release /新版发布包目录
版本切换：cosmesh self rollback（回到上一个已安装版本）
卸载程序：cosmesh self uninstall（保留数据及自定义 Skill）

0.4 将托管 cosbase Skill 完整备份后停用；自定义内容备份位置见回执。cosmesh 自定义修改仍报告冲突。升级失败恢复当前入口。
发布校验：shasum -a 256 -c SHA256SUMS。校验值用于检查文件完整性；发布来源由下载渠道确定。
业务能力以包内 RELEASE_NOTES 和 cosmesh capabilities 为准。远端同步使用维护者配置的 Hub 与本人已有服务器身份。
''' % (version,target_platform,machine,*python_version)
        if target_platform == 'win32':
            instructions = r"""# CosMesh Windows 客户端

适用于 Windows 11 x64、64 位 CPython 3.12 和 Git for Windows。离线依赖随包提供。使用 Hub 时还需 OpenSSH、已有的本人账号和服务发现文件路径。

在本目录打开 PowerShell，首次安装后会显示快速上手说明。

```powershell
$prefix = Join-Path $env:LOCALAPPDATA 'CosMesh'
py -3.12 -I -X utf8 .\install.py install --prefix "$prefix" --skills cosmesh
$cm = Join-Path $prefix 'bin\cosmesh.cmd'
& $cm self status
& $cm guide show upgrade
```

以后解压新包，在新包目录运行下面的命令。自定义安装应继续使用原程序入口。

```powershell
& $cm client update --release (Get-Location).Path
```

同版重复检查只返回当前状态。如已安装同版号的 0.7.0 预览包，请把正式程序装进独立前缀；原资料可用原数据目录或 `--root` 接续。新开 Agent 会话可加载更新的托管 Skill。
"""
        (payload/'README.md').write_text(instructions,encoding='utf-8')
        changelog=(ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
        notes=re.search(r'^## '+re.escape(version)+r'\n(.*?)(?=^## |\Z)',changelog,re.MULTILINE|re.DOTALL)
        if notes:
            (payload/'RELEASE_NOTES.md').write_text('# '+version+'\n'+notes.group(1),encoding='utf-8')
        files = {p.relative_to(payload).as_posix():sha(p) for p in sorted(payload.rglob('*')) if p.is_file()}
        manifest = {'format':1,'product':'cosmesh','version':version,'release_id':release_id,
                    'python':'%d.%d'%python_version,'platform':target_platform,'machine':machine,
                    'schema_write':([1,2,3] if tuple(map(int,version.split('.'))) >= (0,3,0) else [1,2] if tuple(map(int,version.split('.'))) >= (0,2,0) else [1]),'wheel':'wheels/'+wheel.name,
                    'wheels':['wheels/'+p.name for p in sorted(wheels.glob('*.whl'))], 'files':files,
                    'thread_lifecycle_version': 2 if tuple(map(int,version.split('.'))) >= (0,3,7) else 1}
        (payload/'release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        sums = '\n'.join(sha(p)+'  '+p.relative_to(payload).as_posix() for p in sorted(payload.rglob('*')) if p.is_file())+'\n'
        (payload/'SHA256SUMS').write_text(sums,encoding='utf-8')
        os_archive = staging/(release_id+'.zip')
        with zipfile.ZipFile(os_archive,'w',zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(payload.rglob('*')):
                if p.is_file():
                    archive.write(p,release_id+'/'+p.relative_to(payload).as_posix())
        # All artifacts are complete before they become visible under the release name.
        payload.rename(target)
        os_archive.rename(archive_target)
        print(json.dumps({'version':version,'directory':str(target.resolve()),'archive':str(archive_target.resolve()),
                          'sha256':sha(archive_target)},ensure_ascii=False))


if __name__=='__main__':
    main()
