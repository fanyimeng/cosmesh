"""Stable names, private project bindings and shared interaction preference."""
import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.threads import Workbench,initialize
from cosmesh.thread_names import set_names,view
from cosmesh.agent_projects import bind,active
from cosmesh.thread_lifecycle import locate

class FastProjectsNamesTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        env=patch.dict(os.environ,COSMESH_PROFILE_DIR=str(self.root/'profile'),COSMESH_PAGE_CACHE=str(self.root/'cache'),COSMESH_PAGE_BACKGROUND='0')
        env.start();self.addCleanup(env.stop)
        initialize(self.root/'work','alice');self.w=Workbench(self.root/'work');self.w.load()
        self.tid=self.w.create('输入误差分析','研究观测误差及其对结果的影响。比较模型与测量。',naming={'english':'Error Study','chinese':'误差分析','intro':'评估测量误差对分析结果的影响。','aliases':[]})['id'];self.w.load()
    def call(self,*args,ok=True):
        with redirect_stdout(io.StringIO()) as out:code=main([*args,'--root',str(self.w.base.root),'--json'])
        value=json.loads(out.getvalue());self.assertEqual(code==0,ok,value);return value
    def test_preference_default_shared_toggle_override_and_profile_preservation(self):
        p=self.root/'profile';p.mkdir(mode=0o700);profile=p/'profile.md';profile.write_text('private profile')
        self.assertTrue(self.call('thread','page','--ready')['fast_mode']);self.assertFalse((p/'preferences.json').exists())
        self.assertFalse(self.call('client','mode','normal')['fast_mode'])
        self.assertTrue(self.call('thread','page','--ready','--fast')['fast_mode'])
        self.assertFalse(self.call('client','mode')['fast_mode'])
        self.assertEqual(profile.read_text(),'private profile')
        self.assertEqual((p/'preferences.json').stat().st_mode & 0o777,0o600)
        self.assertTrue(self.call('client','mode','fast')['fast_mode'])
        (p/'preferences.json').write_text('bad')
        self.assertTrue(self.call('thread','page','--ready')['fast_mode'])
        self.call('client','mode',ok=False)
    def test_rename_keeps_aliases_and_summary_distinct(self):
        w=self.w;v=view(w,self.tid)['version']
        summary=w.append(self.tid,'## 整体介绍\n研究误差传播，确定适用条件。\n\n## 下一步\n核验误差。','summary')['id'];w.load()
        set_names(w,self.tid,english='Error Model',chinese='误差模型',intro='建立可复核的误差模型。',expect=v);w.load()
        self.assertEqual(w.find('ERROR STUDY')['doc']['meta']['id'],self.tid)
        self.assertEqual(w.find('误差模型')['doc']['meta']['id'],self.tid)
        version=w.ref(w.find(self.tid))['version'];w.edit(self.tid,version,title='新的详细工作名称');w.load()
        self.assertEqual(w.find('输入误差分析')['doc']['meta']['id'],self.tid)
        ready=self.call('thread','page','--continue','Error Model','--ready')['data']
        self.assertIn('Error Model · 误差模型',ready['document']);self.assertIn('建立可复核的误差模型。',ready['document'])
        self.assertTrue(ready['record_preview']['body_complete']);self.assertEqual(ready['record_preview']['total'],1)
        self.assertNotIn('cosmesh.thread-names',ready['document'])
        detailed=self.call('thread','page','误差分析','--level','status')['data']
        self.assertEqual(detailed['status']['summary']['source']['id'],summary)
        self.assertIn('研究误差传播',detailed['thread']['overall']['text'])
        with self.assertRaises(Error):set_names(w,self.tid,chinese='过期',expect=v)
        other=w.create('其他误差研究','另一个独立工作。',naming={'english':'Error Model','chinese':None,'intro':None,'aliases':[]})['id'];w.load()
        with self.assertRaises(Error) as e:w.find('Error Model')
        self.assertEqual(e.exception.code,'AMBIGUOUS_ID')
        self.call('thread','page','Error Model','--ready',ok=False)
        self.assertEqual(w.find(other)['doc']['meta']['id'],other)
    def test_private_projects_independent_of_sessions_across_providers(self):
        w=self.w;head=w.base.head;host=socket.gethostname()
        observation={'format':1,'source':'supported_host_api','observed_at':'2026-01-02T12:00:00+00:00','projects':[{'agent':'codex','host':host,'id':'native-id','name':'Example'}]}
        bind(w,self.tid,'codex','Example','native-id',host=host,observation=observation)
        bind(w,self.tid,'DeepSeek Harness','DSH workspace',kind='workspace')
        bind(w,self.tid,'opencode','Example folder',kind='directory',workdir=str(self.root))
        self.assertEqual(w.base.head,head);self.assertEqual(w.client['bindings'],[])
        self.assertEqual(len(active(w,self.tid)),3)
        self.assertEqual(locate(w,host=host,agent='codex',project_id='native-id')['candidates'][0]['thread']['id'],self.tid)
        ready=self.call('thread','page','--ready')['data'];self.assertIn('Codex · Example',ready['document']);self.assertIn('DSH · DSH workspace',ready['document']);self.assertIn('OpenCode · Example folder',ready['document'])
        self.assertEqual(ready['document'].count('| 工作 |'),1)
        bind(w,self.tid,'codex',project_id='native-id',host=host,remove=True)
        self.assertEqual(len(active(w,self.tid)),2);self.assertEqual(len(w.client['project_bindings']),4)
    def test_create_names_cli_one_commit_and_find_search(self):
        value=self.call('thread','create','--title','保存文献资料','--english','Paper Shelf','--chinese','文献架','--intro','保存可引用的研究资料。','--body','研究资料长期整理。')['data']
        self.assertIn('naming_record',value)
        self.w.load();self.assertEqual(self.w.find('paper shelf')['doc']['meta']['id'],value['id'])
        found=self.call('search','query','Paper Shelf','--no-legacy','--kind','thread')['data']
        self.assertTrue(any(x['source']['id']==value['id'] for x in found['hits']))


from test_threads import ThreadFixture
class SharedNamesProjectsTests(ThreadFixture):
    def test_shared_naming_uses_existing_protocol_and_hides_private_project(self):
        tid=self.shared('alice','共享输入检查','核验输入和版本。')
        with self.use('alice') as w:
            set_names(w,tid,english='Input Check',chinese='输入检查',intro='核验共享输入的版本。');w.load()
            bind(w,tid,'dsh','private-workspace',kind='workspace')
            self.assertTrue(w.synchronize()['synchronized'])
        self.update('bob')
        with self.use('bob') as w:
            self.assertEqual(w.find('输入检查')['doc']['meta']['id'],tid)
            self.assertEqual(view(w,tid)['english'],'Input Check')
            with self.assertRaises(Error):set_names(w,tid,chinese='越权修改')
        with patch.dict(os.environ,COSMESH_PAGE_CACHE=str(self.root/'page-cache'),COSMESH_PAGE_BACKGROUND='0'):
            with redirect_stdout(io.StringIO()) as out:
                code=main(['thread','page','--root',str(self.wbs['alice'].base.root),'--scope','mesh','--ready','--json'])
            self.assertEqual(code,0,out.getvalue());shared=json.loads(out.getvalue())['data']
            self.assertIn('Input Check',shared['document']);self.assertNotIn('private-workspace',json.dumps(shared))
            self.assertNotIn('project_bindings',json.dumps(shared))
