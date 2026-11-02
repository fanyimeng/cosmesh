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
    args = parser.parse_args()
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
                    assert package.read(release_id+'/'+str(item.relative_to(candidate)))==item.read_bytes()
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
            wheel = next(build_dir.glob('cosmesh-*.whl'))
        version = wheel_version(wheel)
        release_id = 'cosmesh-%s-%s-%s-py%d%d' % (version,sys.platform,platform.machine(),*sys.version_info[:2])
        target = args.output/release_id
        archive_target = args.output/(release_id+'.zip')
        if target.exists() or archive_target.exists():
            raise FileExistsError('Release artifacts are immutable: '+release_id)
        shutil.copy2(wheel,wheels/wheel.name)
        run([sys.executable,'-m','pip','download','--only-binary=:all:','--no-deps',
             '--dest',wheels,'-r',ROOT/'requirements-release.txt'])
        payload = staging/'payload'
        shutil.copy2(ROOT/'tools/release_installer.py',payload/'install.py')
        agent = payload/'agent'
        agent.mkdir()
        shutil.copy2(ROOT/'tools/remote_bootstrap.py',agent/'bootstrap.py')
        for helper in ('mesh_agent_control.py', 'prepare_thread_preview.py'):
            shutil.copy2(ROOT/'tools'/helper, agent/helper)
        shutil.copy2(ROOT/'cosmesh/resources/remote-install.sh',agent/'install.sh')
        instructions = '''# CosMesh / CosBase 用户发布包 %s

适用环境：%s / %s / Python %d.%d，另需 Git。依赖 wheels 随包提供，安装不联网。

先解压发布包，在本目录打开终端。首次安装（可先加 --dry-run 预览）：

    python3 install.py install --skills both

默认安装到 ~/.local/opt/cosmesh，选装 Skill 位于 ~/.agents/skills。
可用 --prefix 和 --skills-dir 指定独立测试目录，--skills none 跳过 Skill。

查看安装：

    ~/.local/opt/cosmesh/bin/cosmesh self status
    ~/.local/opt/cosmesh/bin/cosbase --version

首次建立工作台（0.2 的 Thread 模型）：

    ~/.local/opt/cosmesh/bin/cosmesh thread init --json

默认使用当前系统账号。默认数据位于 ~/.local/share/cosmesh，升级和卸载保留数据。
新终端可设置 PATH：export PATH="$HOME/.local/opt/cosmesh/bin:$PATH"

下载并解压新版后，在新包目录执行：

    python3 install.py upgrade

也可以用已安装命令：cosmesh self upgrade --release /新版发布包目录
版本切换：cosmesh self rollback（回到上一个已安装版本）
卸载程序：cosmesh self uninstall（保留数据及自定义 Skill）

只有安装器管理且未被修改的 Skill 会随版本更新。升级失败保留当前版本。
发布校验：shasum -a 256 -c SHA256SUMS。校验值用于检查文件完整性；发布来源由下载渠道确定。
业务能力以包内 RELEASE_NOTES 和 cosmesh capabilities 为准。远端同步需管理员配置受限 SSH Hub、公钥身份与仓库权限。
''' % (version,sys.platform,platform.machine(),*sys.version_info[:2])
        (payload/'README.md').write_text(instructions,encoding='utf-8')
        changelog=(ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
        notes=re.search(r'^## '+re.escape(version)+r'\n(.*?)(?=^## |\Z)',changelog,re.MULTILINE|re.DOTALL)
        if notes:
            (payload/'RELEASE_NOTES.md').write_text('# '+version+'\n'+notes.group(1),encoding='utf-8')
        files = {str(p.relative_to(payload)):sha(p) for p in sorted(payload.rglob('*')) if p.is_file()}
        manifest = {'format':1,'product':'cosmesh','version':version,'release_id':release_id,
                    'python':'%d.%d'%sys.version_info[:2],'platform':sys.platform,'machine':platform.machine(),
                    'schema_write':([1,2,3] if tuple(map(int,version.split('.'))) >= (0,3,0) else [1,2] if tuple(map(int,version.split('.'))) >= (0,2,0) else [1]),'wheel':'wheels/'+wheel.name,
                    'wheels':['wheels/'+p.name for p in sorted(wheels.glob('*.whl'))], 'files':files,
                    'thread_lifecycle_version': 2 if tuple(map(int,version.split('.'))) >= (0,3,7) else 1}
        (payload/'release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        sums = '\n'.join(sha(p)+'  '+str(p.relative_to(payload)) for p in sorted(payload.rglob('*')) if p.is_file())+'\n'
        (payload/'SHA256SUMS').write_text(sums,encoding='utf-8')
        os_archive = staging/(release_id+'.zip')
        with zipfile.ZipFile(os_archive,'w',zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(payload.rglob('*')):
                if p.is_file():
                    archive.write(p,release_id+'/'+str(p.relative_to(payload)))
        # All artifacts are complete before they become visible under the release name.
        payload.rename(target)
        os_archive.rename(archive_target)
        print(json.dumps({'version':version,'directory':str(target.resolve()),'archive':str(archive_target.resolve()),
                          'sha256':sha(archive_target)},ensure_ascii=False))


if __name__=='__main__':
    main()
