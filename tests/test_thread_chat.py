"""Authored summaries survive real hooks; one cached read feeds chat and TUI."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.knowledge import Knowledge
from cosmesh.thread_chat import document, screen_lines, summary, width, with_summary
from cosmesh.threads import Workbench
from test_threads import ThreadFixture


class SummaryFormatTests(unittest.TestCase):
    def test_flexible_length_original_details_and_terminal_controls(self):
        text = '完成输入核对。报告已补齐。代码待补。下一步核对版本。'
        body = with_summary('详细正文\n## 更多依据\n原始结果保留。', text)
        self.assertEqual(summary(body)['text'],text)
        self.assertIn('原始结果保留',body)
        self.assertEqual(summary('旧版记录')['origin'],'legacy_excerpt')
        self.assertEqual(summary(body)['origin'],'author')
        from cosmesh.errors import Error
        for bad in ('','x'*4001,'## 嵌套标题'):
            with self.assertRaises(Error): with_summary('正文',bad)
        from cosmesh.thread_chat import wrap
        self.assertNotIn('\x1b',''.join(wrap('你好\x1b[31m\x00世界',20)))


class ChatTests(ThreadFixture):
    participants = ('alice','bob')
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ,COSMESH_PAGE_CACHE=str(self.root/'cache'),COSMESH_PAGE_BACKGROUND='0',COSMESH_HUB_PAGE_CACHE=str(self.root/'hub-cache'))
        env.start();self.addCleanup(env.stop)
        self.hub.initialize_community(self.root/'library')
        for user, name in {'alice':'张三','bob':'李四'}.items():
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
            with self.use(user) as w:w.discover()
        self.tid = self.shared('bob','云研究','研究分子云的形成机制。目标是检验动力学解释。')
        with self.use('alice') as w:w.synchronize(publish=False)

    def call(self,user,*args,ok=True):
        with redirect_stdout(io.StringIO()) as output:
            code=main(['thread',*args,'--root',str(self.wbs[user].base.root),'--json'])
        value=json.loads(output.getvalue());self.assertEqual(code==0,ok,value)
        return value

    def test_authored_roundtrip_cached_request_conversation_pagination_and_alignment(self):
        generation=self.root/'generation.json';generation.write_text(json.dumps({'tool':'test-agent','model':'synthetic'}))
        arguments=('request',self.tid,'--to','bob','--body','详细需求：'+'限定合成输入。'*200,
                   '--interaction-summary','请补齐报告和小型结果，代码提供固定版本。','--generation-file',str(generation),
                   '--idempotency-key','summary-request','--sync','--ready')
        request=self.call('alice',*arguments)['data']['result']
        replay=self.call('alice',*arguments)['data']['result']
        self.assertEqual(request['id'],replay['id']);self.assertTrue(replay['replayed'])
        with self.use('bob') as w:w.synchronize(publish=False)
        reply=self.call('bob','reply',request['id'],'--body','详细结果：'+'只应按需读取。'*300,
                        '--interaction-summary','报告已入库，固定引用已补。代码仓库仍待补充。','--sync')['data']['result']
        self.call('bob','note',self.tid,'--body','无关普通记录','--sync')
        store=self.hub.repo(self.repos['bob'])
        documents=store.documents(store.snapshot(store.git('rev-parse','main'))[1])
        self.assertEqual(summary(documents[reply['id']]['body'])['text'],'报告已入库，固定引用已补。代码仓库仍待补充。')
        self.assertEqual(documents[request['id']]['meta']['generation']['tool'],'test-agent')
        self.assertEqual(documents[request['id']]['meta']['author'],'alice')
        synchronize=Workbench.synchronize;calls=[]
        def pull(wb,*args,**kwargs):calls.append(kwargs);return synchronize(wb,*args,**kwargs)
        with patch.object(Workbench,'synchronize',pull):
            data=self.call('alice','chat',request['id'],'--require-fresh')['data']
        self.assertEqual(calls,[{'publish':False}])
        self.assertEqual([m['side'] for m in data['messages']],['right','left'])
        self.assertEqual([m['author_name'] for m in data['messages']],['张三','李四'])
        self.assertTrue(all(m['interaction_summary']['origin']=='author' for m in data['messages']))
        self.assertNotIn('详细结果',json.dumps(data,ensure_ascii=False))
        self.assertNotIn('document',data)  # Summary text is returned once.
        self.assertEqual(data['total'],2)
        self.assertEqual(data['requests'][0]['state'],'pending')
        self.assertEqual(data['requests'][0]['response_count'],1)
        with patch.object(Workbench,'load',side_effect=AssertionError('no source reload')), \
             patch.object(Knowledge,'download',side_effect=AssertionError('no original download')), \
             patch('cosmesh.spaces.rpc',side_effect=AssertionError('no network')):
            warm=self.call('alice','chat',request['id'])['data']
        self.assertEqual(data,warm)
        older=self.call('alice','chat',request['id'],'--limit','1','--offset','1')['data']
        self.assertEqual(older['messages'][0]['id'],request['id'])
        self.assertIn('代码仓库仍待补充',document(data))
        lines=screen_lines(data,79)
        self.assertTrue(all(width(line)<=79 for _,line in lines))
        own=next(line for index,line in lines if index==0 and line.strip())
        other=next(line for index,line in lines if index==1 and line.strip())
        self.assertGreater(len(own)-len(own.lstrip()),len(other)-len(other.lstrip()))
        check=self.call('alice','check',request['id'])['data']
        self.assertIn('交互摘要',check['document'])
        self.assertNotIn('详细结果',check['document'])
        # The original full detail remains available through the existing read.
        self.assertIn('详细结果',self.call('alice','show',reply['id'])['data']['body'])
        contact=self.call('alice','chat','--with','李四')['data']
        self.assertEqual(contact['total_rounds'],1)
        self.assertEqual([m['side'] for m in contact['messages']],['right','left'])
        self.assertEqual(contact['messages'][-1]['request']['state'],'pending')
        self.assertTrue(all(m['thread_name']=='云研究' for m in contact['messages']))
        self.assertTrue(all('local_time' in m for m in contact['messages']))
        self.assertNotIn('无关普通记录',json.dumps(contact,ensure_ascii=False))
        expanded=self.call('alice','chat','--with','李四','--include-progress')['data']
        self.assertTrue(any(m['shared_progress'] for m in expanded['messages']))
        # Content updates prepare the server product. The subsequent latest
        # read is one RPC with no local load, Git fetch, library or source read.
        from cosmesh.hub_pages import build
        build(self.hub)
        from cosmesh.spaces import rpc as actual_rpc
        with patch.object(Workbench,'load',side_effect=AssertionError('prepared read does not load')), \
             patch.object(Workbench,'synchronize',side_effect=AssertionError('no Git synchronization')), \
             patch.object(Knowledge,'download',side_effect=AssertionError('no attachment download')), \
             patch('cosmesh.hub.BareStore.git',side_effect=AssertionError('RPC reads no Git')), \
             patch('cosmesh.spaces.rpc',wraps=actual_rpc) as calls:
            prepared=self.call('alice','chat','--with','李四','--require-fresh')['data']
        self.assertEqual(calls.call_count,1)
        self.assertEqual(prepared['transport'],'prepared_hub_document')
        self.assertTrue(prepared['freshness']['server_current'])
        self.assertEqual(prepared['total_rounds'],1)
        with patch('cosmesh.spaces.rpc',side_effect=AssertionError('cached document requires no network')), \
             patch.object(Workbench,'load',side_effect=AssertionError('no load')):
            self.assertEqual(self.call('alice','chat','--with','李四')['data'],prepared)
        ref=prepared['source_index'][prepared['messages'][-1]['parts'][-1]['source']]
        detail=self.call('alice','read',ref['id']+'@'+ref['version'],'--repo',ref['repo'],'--chars','100')['data']
        self.assertEqual(detail['source'],ref)
        self.assertTrue(detail['truncated'])
        self.call('bob','reply',request['id'],'--body','进一步的详细说明。',
                  '--interaction-summary','补充核验已完成。还需确认代码版本。','--sync')
        with self.use('alice') as w:w.synchronize(publish=False)
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'), \
             patch.object(Workbench,'load',side_effect=AssertionError('request reads the prior product')), \
             patch('cosmesh.spaces.rpc',side_effect=AssertionError('no extra RPC')):
            pending=self.call('alice','chat','--with','李四')['data']
        self.assertTrue(pending['freshness']['updating'])
        self.assertIn('上次内容',document(pending))

    def test_legacy_excerpt_summary_file_and_scope_do_not_publish_private_content(self):
        with self.use('alice') as w:
            private=w.create('私人工作','private-purpose')['id'];w.load()
            w.append(private,'private-secret-body')
        note=self.call('bob','note',self.tid,'--body','旧记录保留原文。','--sync')['data']['result']
        brief=self.root/'brief.txt';brief.write_text('补充了一项约束。后续按新约束执行。')
        revised=self.call('bob','correct',note['id'],'--body','完整更正依据。','--interaction-summary-file',str(brief),'--sync')['data']['result']
        with self.use('alice') as w:w.synchronize(publish=False)
        packet=self.call('alice','chat',self.tid,'--scope','mesh')['data']
        old=next(m for m in packet['messages'] if m['id']==note['id'])
        self.assertEqual(old['interaction_summary']['origin'],'legacy_excerpt')
        self.assertEqual(packet['source_index'][old['superseded_by'][0]]['id'],revised['id'])
        self.assertNotIn('private-secret',json.dumps(packet))
        self.assertNotIn(str(self.root),json.dumps(packet))
        failure=self.call('alice','chat',private,'--scope','mesh',ok=False)
        self.assertEqual(failure['error']['code'],'NOT_FOUND')
        self.assertNotIn(str(self.root),json.dumps(failure))
