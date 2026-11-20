"""Real personal relay and independent work stores, with deterministic native turns."""
from unittest.mock import patch, Mock
import unittest

from tests import test_thread_personal as fixtures
from cosmesh.collector import enable_sync
from cosmesh.errors import Error
from cosmesh.personal import Personal
from cosmesh.personal_relay import Relay
from cosmesh.thread_tasks import rpc, run_once


class Native:
    def __init__(self):
        self.ready = True
        self.started = []
        self.result = {'state':'completed','result':'工作完成。\n'}
    def call(self, method, task, **kwargs):
        if method == 'inspect':
            return {'ready':self.ready, 'cursor':7}
        if method == 'start':
            self.started.append(task['id'])
            return {}
        return self.result


class Assignments(unittest.TestCase):
    use = fixtures.ThreadPersonalTests.use
    work = fixtures.ThreadPersonalTests.work

    def setUp(self):
        fixtures.ThreadPersonalTests.setUp(self)
        self.mac = (self.a, 'codex', 'chat-a', 'mac')
        self.server = (self.b, 'dsh', 'session-b', 'server')
        for wb, agent, session, host in (self.mac, self.server):
            with self.use(wb) as p:
                if wb is self.b:
                    p.restore(self.t)
                wb.bind(self.t,agent,str(self.root),session,host)
                p.synchronize(self.t)
                enable_sync(wb,self.t,True)

    def send(self, source, target, key):
        return rpc(Personal(source[0]),'send',thread=self.t,device=target[3],agent=target[1],
                   session=None,key=key,body='请执行 '+key)

    def step(self, target, model):
        wb, agent, session, host = target
        return run_once(wb,self.t,agent,session,host,model)

    def test_bidirectional_fifo_idle_and_offline(self):
        idle = Mock()
        self.assertEqual(self.step(self.server,idle)['state'],'idle')
        idle.call.assert_not_called()
        first = self.send(self.mac,self.server,'one')
        self.assertEqual(self.send(self.mac,self.server,'one'),first)
        second = self.send(self.mac,self.server,'two')
        native = Native()
        native.ready = False
        self.assertEqual(self.step(self.server,native)['state'],'busy')
        self.assertEqual(native.started,[])
        native.ready = True
        for task in (first,second):
            self.assertEqual(self.step(self.server,native)['task'],task['id'])
            self.assertEqual(self.step(self.server,native)['status'],'completed')
        reverse = self.send(self.server,self.mac,'back')
        self.step(self.mac,native)
        self.assertEqual(self.step(self.mac,native)['status'],'completed')
        self.assertEqual(native.started,[t['id'] for t in (first,second,reverse)])
        with self.use(self.a) as p:
            p.synchronize(self.t,pull=True)
            self.assertEqual(len(self.a.records(self.t)),3)
            texts=''.join(r['doc']['body'] for r in self.a.records(self.t))
            self.assertTrue(all(k in texts for k in native.started))

    def test_all_four_endpoints_route_by_device_and_agent_even_with_matching_session_names(self):
        endpoints = (self.mac, (self.a,'dsh','session-b','mac'),
                     (self.b,'codex','chat-a','server'), self.server)
        for wb, agent, session, host in endpoints:
            with self.use(wb) as p:
                wb.bind(self.t,agent,str(self.root),session,host)
                p.synchronize(self.t)
        first = {}
        for source in endpoints:
            for target in endpoints:
                if source == target:
                    continue
                key = '-'.join((source[3],source[1],target[3],target[1]))
                row = self.send(source,target,key)
                self.assertEqual((row['host'],row['agent'],row['session']), (target[3],target[1],target[2]))
                first.setdefault(target,row['id'])
        for target, task_id in first.items():
            wb, agent, session, host = target
            p = Personal(wb)
            task = rpc(p,'next',thread=self.t,host=host,agent=agent,session=session,
                       device=p.state['device'],replica=p.state['replica'])['task']
            self.assertEqual(task['id'],task_id)

    def test_lost_host_reply_and_retry_after_result_sync(self):
        task = self.send(self.mac,self.server,'once')
        native = Native()
        original = native.call
        def lost(method, **kwargs):
            result = original(method, **kwargs)
            if method == 'start':
                raise OSError('lost acknowledgement')
            return result
        with patch.object(native,'call',side_effect=lost), self.assertRaises(OSError):
            self.step(self.server,native)
        native.result = {'state':'unconfirmed'}
        self.assertEqual(self.step(self.server,native)['state'],'unconfirmed')
        self.assertEqual(self.send(self.mac,self.server,'once')['status'],'unconfirmed')
        native.result = {'state':'completed','result':'ok'}
        def fail_finish(personal,verb,**kwargs):
            if verb == 'finish':
                raise OSError('offline before queue confirmation')
            return rpc(personal,verb,**kwargs)
        with patch('cosmesh.thread_tasks.rpc',side_effect=fail_finish),self.assertRaises(OSError):
            self.step(self.server,native)
        self.assertEqual(self.step(self.server,Mock())['status'],'completed')
        self.assertEqual(native.started,[task['id']])
        with self.use(self.b):
            self.assertEqual(len(self.b.records(self.t)),1)

    def test_identity_ambiguity_pause_and_competing_claim(self):
        with self.use(self.b) as p:
            self.b.bind(self.t,'dsh',str(self.root),'second','server')
            p.synchronize(self.t)
        with self.assertRaisesRegex(Error,'Select one'):
            self.send(self.mac,self.server,'ambiguous')
        p=Personal(self.b)
        task=rpc(p,'send',thread=self.t,device='server',agent='dsh',session='session-b',key='one',body='test')
        with self.assertRaisesRegex(Error,'operation key'):
            self.send(self.mac,self.server,'one')
        with self.assertRaises(Error):
            Relay(self.hub,'other-user').dispatch({'method':'personal_task_list','thread':self.t})
        route=dict(thread=self.t,device=p.state['device'],agent='dsh',host='server',session='session-b',replica=p.state['replica'])
        self.assertIsNone(rpc(p,'next',**dict(route,host='other-node'))['task'])
        self.assertTrue(rpc(p,'claim',**route,id=task['id'],cursor=-1)['claimed'])
        self.assertFalse(rpc(p,'claim',**route,id=task['id'],cursor=-1)['claimed'])
        self.assertIsNone(rpc(p,'next',**dict(route,replica='other-replica'))['task'])
        with self.use(self.b):
            enable_sync(self.b,self.t,False)
        with self.assertRaisesRegex(Error,'Enable personal sync'):
            self.step(self.server,Mock())


class CodexQueueTests(unittest.TestCase):
    def test_dispatch_uses_native_queue_when_human_starts_between_inspection_and_send(self):
        from pathlib import Path
        from cosmesh.task_hosts import Codex
        root = str(Path.cwd())
        task = dict(id='stable-task', thread='W-test', body='work', cursor=-1)
        native = Codex()
        methods = []
        def host(method, params):
            methods.append(method)
            if method == 'thread/read':
                return {'thread':{'id':'chat','cwd':root,'status':{'type':'idle'}}}
            if method == 'thread/queue/list':
                return {'data':[], 'nextCursor':None}
            if method == 'thread/queue/add':
                self.assertEqual(params['clientUserMessageId'], task['id'])
                return {'queuedSubmission':{'clientUserMessageId':task['id']}}
            self.fail('The receiver must leave native turn admission to the queue.')
        args = dict(session='chat',workdir=root,task=task)
        with patch.object(native,'rpc',side_effect=host):
            self.assertTrue(native.call('inspect',**args)['ready'])
            native.call('start',**args)
        self.assertEqual(methods,['thread/read','thread/queue/list','thread/queue/add'])

    def test_recovery_only_returns_the_addressed_turn_final_answer(self):
        from cosmesh.task_hosts import codex_evidence
        task = dict(id='task', thread='W-test', body='work')
        def turn(key, reply):
            return {'status':'completed','items':[{'type':'userMessage','clientId':key},
                    {'type':'agentMessage','phase':'commentary','text':'working'},
                    {'type':'agentMessage','phase':'final_answer','text':reply}]}
        turns = [turn('other','another result'), turn('task','my result')]
        self.assertEqual(codex_evidence(turns,task),{'state':'completed','result':'my result'})
        turns[-1]['items'].append({'type':'userMessage','clientId':'second'})
        self.assertEqual(codex_evidence(turns,task)['state'],'unconfirmed')
