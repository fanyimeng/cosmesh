"""A real local HTTP adapter and durable coordinator recovery, without a live LLM."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_threads import ThreadFixture
from cosmesh.errors import Error
from cosmesh.mesh_agent import Coordinator


class AgentTests(ThreadFixture):
    def setup_agent(self):
        thread = self.shared('alice','检查工作','可公开背景')
        with self.use('alice') as a:
            private = a.create('私人事项','PRIVATE-DO-NOT-SEND');a.load()
            a.bind(thread,'test-agent',str(self.root),session_id='PRIVATE-SESSION')
        self.agent=Coordinator(self.root/'agent');self.agent.initialize(self.wbs['alice'].base.root)
        return thread

    def test_waiting_is_ready_and_queue_is_private_idempotent(self):
        self.setup_agent()
        result=self.agent.once()
        self.assertTrue(result['ready']);self.assertEqual(result['state'],'waiting_for_model')
        jobs=self.agent.jobs();self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['attempts'],0)
        self.assertNotIn('PRIVATE-DO-NOT-SEND',json.dumps(jobs))
        self.assertNotIn('PRIVATE-SESSION',json.dumps(jobs))
        restarted=Coordinator(self.root/'agent');restarted.once()
        self.assertEqual(len(restarted.jobs()),1)
        self.assertEqual((self.root/'agent/queue.sqlite3').stat().st_mode & 0o777,0o600)

    def test_real_http_model_review_and_publish_without_self_trigger(self):
        thread=self.setup_agent();calls=[]
        class Handler(BaseHTTPRequestHandler):
            invalid_sources=False
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(data)
                payload=json.loads(data['messages'][1]['content'])
                output={'title':'当前进展','body':'现有记录给出了背景，检查结果尚待补充。','source_ids':['unknown-source'] if self.invalid_sources else [payload['thread']['id']]}
                raw=json.dumps({'choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)}}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        endpoint='http://127.0.0.1:%s/v1/chat/completions'%server.server_address[1]
        self.agent.configure(endpoint,'test-model',publish=True)
        result=self.agent.once();self.assertEqual(result['jobs'].get('done'),1,result)
        self.assertEqual(len(calls),1)
        self.assertNotIn('PRIVATE-',json.dumps(calls))
        self.agent.once();self.assertEqual(len(calls),1)
        with self.use('alice') as a:
            a.load();generated=[x for x in a.records(thread) if x['doc']['meta'].get('agent')=='mesh-agent']
            self.assertEqual(len(generated),1)
            self.assertTrue(a.accepted(generated[0]))
        Handler.invalid_sources=True
        with self.use('alice') as a:
            a.append(thread,'新增人工进展');a.load();a.synchronize()
        rejected=self.agent.once()
        self.assertEqual(rejected['jobs'].get('failed'),1)
        self.assertEqual(self.agent.jobs()[0]['error']['code'],'MODEL_RESPONSE_INVALID')

    def test_invalid_sources_rejected_and_leases_are_exclusive(self):
        self.setup_agent();self.agent.scan()
        self.agent.configure('http://127.0.0.1:1/v1/chat/completions','test-model')
        first=self.agent.claim();self.assertIsNotNone(first)
        other=Coordinator(self.root/'agent');self.assertIsNone(other.claim())
        with self.agent.database() as db:db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-1,first['id']))
        replacement=other.claim();self.assertEqual(replacement['id'],first['id'])
        with self.assertRaises(Error):self.agent.finish(first,'review',{'body':'old'})
        other.finish(replacement,'failed',error={'code':'test'})
        self.agent.retry(first['id']);self.assertEqual(self.agent.jobs()[0]['attempts'],0)

    def test_model_failure_has_bounded_retries_and_keeps_materials(self):
        self.setup_agent();self.agent.configure('http://127.0.0.1:1/v1/chat/completions','test-model')
        with patch.object(self.agent,'model',side_effect=Error('MODEL_RESPONSE_INVALID','invalid source')) as call:
            for _ in range(5):self.agent.once()
        self.assertEqual(call.call_count,3)
        job=self.agent.jobs()[0];self.assertEqual(job['state'],'failed')
        self.assertIn('thread',job['payload'])

    def test_publication_retry_reuses_persisted_generation(self):
        thread=self.setup_agent();self.agent.configure('http://127.0.0.1:1/v1/chat/completions','test-model',publish=True)
        output={'title':'摘要','body':'待检查','source_ids':[thread]}
        with patch.object(self.agent,'model',return_value=output) as model, patch.object(self.agent,'publish',side_effect=Error('SYNC_PARTIAL','offline')):
            self.agent.once();self.agent.once()
        self.assertEqual(model.call_count,1)
        self.assertEqual(self.agent.jobs()[0]['result'],output)
