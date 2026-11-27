"""Offline acceptance of a managed candidate. All sample data live in a new folder."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(prefix, tests, folder):
    manager = prefix / 'manager.py'
    wrapper = prefix / 'bin' / ('cosmesh.cmd' if os.name == 'nt' else 'cosmesh')
    state = json.loads((prefix / 'state.json').read_text(encoding='utf-8'))
    runtime = prefix / 'versions' / state['current'] / 'runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    workspace = folder / 'work'
    project = folder / '含空格的 project'
    project.mkdir()
    env = dict(os.environ, COSMESH_DATA_DIR=str(folder / 'data'),
               COSMESH_PROFILE_DIR=str(folder / 'profile'), COSMESH_PAGE_CACHE=str(folder / 'page-cache'),
               COSMESH_PAGE_BACKGROUND='0')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'COSMESH_INSTALL_PREFIX', 'VIRTUAL_ENV'):
        env.pop(name, None)

    def run(command):
        result = subprocess.run(command, cwd=folder, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=90)
        if result.returncode:
            error = (result.stdout + result.stderr).decode('utf-8', errors='replace')
            raise RuntimeError(f'{command[-3:]} exited {result.returncode}\n{error[-1500:]}')
        return result.stdout.decode('utf-8')

    def cli(*args):
        text = run([sys.executable, '-I', '-X', 'utf8', str(manager), 'launch', 'cosmesh',
                    '--root', str(workspace), *args, '--json'])
        reply = json.loads(text)
        require(reply.get('ok') is True, str(reply.get('error')))
        return reply['data']

    require(python.is_file(), 'Missing selected candidate runtime')
    require(run([str(wrapper), '--version']).strip() == state['current'], 'Version wrapper failed')
    bad = subprocess.run([str(wrapper), 'candidate-invalid-command', '--json'], cwd=folder,
                         env=env, capture_output=True, timeout=30)
    require(bad.returncode != 0 and json.loads(bad.stdout.decode('utf-8')).get('ok') is False,
            'Launcher did not propagate the CLI error')
    print('PASS  managed launcher and failure exit code')
    cli('thread', 'init', '--user', 'winreview')
    created = cli('thread', 'create', '--title', '中文资料', '--body', '离线验收')
    tid = created['id']
    require(re.fullmatch(r'W-[a-f0-9-]{36}', tid), 'Missing Thread ID')
    cli('thread', 'bind', tid, '--agent', 'codex', '--workdir', str(project))
    asset = project / '观测笔记.txt'
    asset.write_bytes('离线中文样本\n'.encode('utf-8'))
    result = cli('personal', 'asset', 'add', tid, str(asset), '--as', 'notes/观测笔记.txt')
    require(result['id'], 'Missing asset ID')
    print('PASS  Chinese Thread, native project path and registered file')
    profile = cli('profile', 'init')
    cli('profile', 'set', '--expect', profile['version'], '--display-name', '移植验证')
    require(cli('profile', 'show')['fields']['display_name'] == '移植验证', 'Profile readback differs')
    print('PASS  private profile write and readback in separate processes')

    def create(i):
        return cli('thread', 'create', '--title', f'并行 {i}')['id']

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        ids = set(pool.map(create, range(6)))
    rows = cli('thread', 'list', '--scope', 'base', '--offline')
    if isinstance(rows, dict):
        rows = rows['threads']
    require(len(ids) == 6 and ids | {tid} == {r['id'] for r in rows}, 'Concurrent Thread list differs')
    print('PASS  six writers and seven distinct Threads')
    for module in ('test_windows_locks.py', 'test_windows_private.py', 'test_windows_paths.py'):
        require((tests / module).is_file(), f'Missing acceptance module {module}')
        run([str(python), '-I', '-X', 'utf8', '-m', 'unittest', 'discover',
             '-s', str(tests), '-p', module, '-v'])
        print('PASS ', module, '(native ACL cases require Windows)' if module == 'test_windows_private.py' else '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', required=True, type=Path)
    parser.add_argument('--tests', type=Path, default=Path(__file__).resolve().parents[1] / 'tests')
    args = parser.parse_args()
    # Preserve only this newly-created fixture on failure to diagnose it locally.
    parent = Path(tempfile.gettempdir()).resolve()
    folder = Path(tempfile.mkdtemp(prefix='cosmesh-candidate-', dir=parent))
    try:
        verify(args.prefix.resolve(), args.tests.resolve(), folder)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'FAIL  {exc}\nLocal fixture: {folder}', file=sys.stderr)
        return 1
    def retry_readonly(function, path, error):
        # Git may mark its own objects read-only on NTFS.
        if os.name != 'nt' or not isinstance(error[1], PermissionError):
            raise error[1]
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    shutil.rmtree(folder, onerror=retry_readonly)
    print('PASS  offline candidate acceptance')
    return 0


if __name__ == '__main__':
    sys.exit(main())
