"""Exercise real user installs, upgrades and rollback without changing user profiles."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old',type=Path,required=True,help='Unpacked older release')
    parser.add_argument('--new',type=Path,required=True,help='Unpacked newer release')
    args=parser.parse_args()
    old,new=args.old.resolve(),args.new.resolve()
    old_version=json.loads((old/'release.json').read_text())['version']
    new_version=json.loads((new/'release.json').read_text())['version']
    interpreter=Path(sys.executable).resolve()
    source=Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='cosmesh-user-',dir=str(Path(tempfile.gettempdir()).resolve())) as folder:
        home=Path(folder)
        prefix,skills,data=home/'app',home/'skills',home/'documents'
        env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1',COSMESH_PROFILE_DIR=str(home/'profile'),
                 COSMESH_FEEDBACK_DIR=str(home/'feedback'),COSMESH_SERVICE=str(home/'no-service.json'),
                 COSMESH_DATA_DIR=str(home/'spaces'))
        def run(command,json_result=True,success=True):
            result=subprocess.run([str(c) for c in command],capture_output=True,text=True,cwd=home,env=env)
            assert (result.returncode==0)==success,(command,result.stdout,result.stderr)
            return json.loads(result.stdout) if json_result else result.stdout.strip()
        def app(name,*arguments,success=True):
            return run([prefix/'bin'/name,*arguments,'--json'],success=success)
        def secret_app(name, *arguments):
            # Verify inherited pipes survive the real managed-release launcher.
            reader, writer = os.pipe()
            try:
                os.write(writer, b'isolated-release-test-password\n')
                os.close(writer)
                writer = None
                result = subprocess.run([str(prefix/'bin'/name), *map(str, arguments), '--key-fd', str(reader), '--json'],
                                        env=env, cwd=home, text=True, capture_output=True, pass_fds=(reader,))
                assert result.returncode == 0, (result.stdout, result.stderr)
                assert 'isolated-release-test-password' not in result.stdout + result.stderr
                return json.loads(result.stdout)
            finally:
                os.close(reader)
                if writer is not None:
                    os.close(writer)
        preview=run([interpreter,old/'install.py','install','--prefix',prefix,'--skills','both','--skills-dir',skills,'--dry-run','--json'])
        assert preview['data']['dry_run'] and not prefix.exists()
        run([interpreter,old/'install.py','install','--prefix',prefix,'--skills','both','--skills-dir',skills,'--json'])
        assert run([prefix/'bin/cosmesh','--version'],False)==old_version
        assert app('cosmesh','self','status')['data']['version']==old_version
        app('cosbase','--root',data,'init','--user','tester')
        task=app('cosbase','--root',data,'task','create','--title','升级后必须保留的个人事项','--body','用户数据始终独立。')['data']['result']
        before_head=run(['git','-C',data,'rev-parse','HEAD'],False)
        original_skill=(skills/'cosbase/SKILL.md').read_bytes()
        assert str(prefix/'bin/cosbase') in original_skill.decode()
        print('通过：从发布包安装 '+old_version+'，建立真实用户事项；未读取开发版代码。',flush=True)
        # Damaged release artifacts fail before the active version is touched.
        damaged=home/'damaged-release'
        shutil.copytree(new,damaged)
        manifest=json.loads((damaged/'release.json').read_text())
        wheel=damaged/manifest['wheel']
        wheel.write_bytes(wheel.read_bytes()+b'damaged')
        reply=app('cosmesh','self','upgrade','--release',damaged,success=False)
        assert reply['error']['code']=='CHECKSUM_MISMATCH'
        assert app('cosmesh','self','status')['data']['version']==old_version
        # Local Skill edits are never overwritten by upgrades.
        (skills/'cosbase/SKILL.md').write_text('用户自定义内容')
        reply=app('cosmesh','self','upgrade','--release',new,success=False)
        assert reply['error']['code']=='SKILL_CONFLICT'
        assert (skills/'cosbase/SKILL.md').read_text()=='用户自定义内容'
        (skills/'cosbase/SKILL.md').write_bytes(original_skill)
        reply=app('cosmesh','self','upgrade','--release',new)
        assert reply['data']['to']==new_version
        assert run([prefix/'bin/cosbase','--version'],False)==new_version
        state=app('cosmesh','self','status')['data']
        assert state['previous']==old_version
        assert state['skills']['cosbase']['version']==new_version
        assert (skills/'cosbase/SKILL.md').read_bytes()!=original_skill
        assert app('cosbase','--root',data,'doc','show',task['id'])['data']['body']==task['body']
        assert run(['git','-C',data,'rev-parse','HEAD'],False)==before_head
        assert app('cosmesh','capabilities')['data']['installation']['mode']=='managed_release'
        profile_before=None
        feedback_before={}
        if app('cosmesh','capabilities')['data']['features'].get('system_feedback'):
            app('cosbase','feedback','add','--body','卸载后保留的离线系统反馈','--idempotency-key','release-feedback')
            feedback_before={p:p.read_bytes() for p in (home/'feedback/outbox').glob('*.json')}
        if app('cosmesh','capabilities')['data']['features'].get('shared_personal_profile'):
            personal=app('cosbase','profile','init')['data']
            app('cosmesh','profile','set','--expect',personal['version'],'--about','升级回滚后保留的私人档案')
            assert app('cosbase','profile','show')['data']['fields']['about']=='升级回滚后保留的私人档案'
            if app('cosmesh','capabilities')['data']['features'].get('profile_field_encryption'):
                current=app('cosbase','profile','show')['data']
                contact=app('cosbase','profile','set','--expect',current['version'],'--contact','release@example.invalid')['data']
                secret_app('cosbase','profile','encrypt','--field','contact','--expect',contact['version'])
                revealed=secret_app('cosmesh','profile','reveal','--field','contact')['data']
                assert revealed['fields']=={'contact':'release@example.invalid'} and not revealed['persisted']
                assert b'release@example.invalid' not in (home/'profile/profile.md').read_bytes()
            profile_before=(home/'profile/profile.md').read_bytes()
        reply=app('cosbase','--root',prefix/'my-documents','init','--user','tester',success=False)
        assert reply['error']['code']=='PATH_INVALID' and not (prefix/'my-documents').exists()
        doctor=app('cosbase','--root',data,'doctor','--agent','codex')
        assert doctor['data']['agent']['skills'][0]['status']=='installed_managed'
        # Global options before skills install still enter the managed workflow.
        managed=app('cosmesh','--root',data,'skills','install','--agent','opencode','--only','both','--dest',skills)
        assert managed['data']['managed']
        print('通过：坏包和自定义 Skill 均阻止升级；正常升级到 '+new_version+'，事项和 Git 版本不变。',flush=True)
        app('cosmesh','self','rollback')
        assert run([prefix/'bin/cosbase','--version'],False)==old_version
        assert (skills/'cosbase/SKILL.md').read_bytes()==original_skill
        assert app('cosbase','--root',data,'doc','show',task['id'])['data']['id']==task['id']
        app('cosmesh','self','upgrade','--release',new)
        if profile_before is not None:
            assert (home/'profile/profile.md').read_bytes()==profile_before
        if app('cosmesh','capabilities')['data']['features'].get('profile_field_encryption'):
            assert secret_app('cosbase','profile','reveal','--field','contact')['data']['fields']['contact']=='release@example.invalid'
        print('通过：回滚到 '+old_version+' 后可再次升级；程序与 Skill 同步切换。',flush=True)
        # Confirm source-tree independence from the actual installed runtime.
        location=run([prefix/'versions'/new_version/'runtime/bin/python','-I','-c','import cosmesh; print(cosmesh.__file__)'],False)
        assert Path(location).is_relative_to(prefix/'versions'/new_version/'runtime')
        assert str(source) not in location
        (skills/'cosbase/SKILL.md').write_text('卸载时也保留的自定义 Skill')
        (prefix/'user-note.txt').write_text('保留用户额外文件')
        result=app('cosmesh','self','uninstall')
        assert result['data']['data_unchanged']
        assert not (prefix/'bin/cosmesh').exists()
        assert (skills/'cosbase/SKILL.md').read_text()=='卸载时也保留的自定义 Skill'
        assert not (skills/'cosmesh/SKILL.md').exists()
        assert (prefix/'user-note.txt').exists()
        assert (data/'documents'/(task['id']+'.md')).exists()
        if profile_before is not None:
            assert (home/'profile/profile.md').read_bytes()==profile_before
        for path,raw in feedback_before.items():
            assert path.read_bytes()==raw
        assert run(['git','-C',data,'rev-parse','HEAD'],False)==before_head
        print('通过：卸载程序，个人文档、自定义 Skill 和额外文件保留。',flush=True)
    print('完整用户安装 / 升级 / 回滚 / 卸载流程通过，临时体验目录已清理。')


if __name__=='__main__':
    main()
