"""Continuous controls through real peer-identity sockets; SSH launch counts are observable."""
import base64
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.connections import Frames, session, send_frame, control_options
from cosmesh.errors import Error
from cosmesh.knowledge import Knowledge
from cosmesh.store import Store
from cosmesh.threads import Workbench, initialize
from cosmesh import spaces, sync
import test_system_access as fixtures


class FrameTests(unittest.TestCase):
    def test_buffered_frames_preserve_boundaries_and_eof(self):
        left, right = socket.socketpair()
        try:
            send_frame(left, {'text': '中文', 'n': 1}); send_frame(left, {'n': 2}); left.shutdown(socket.SHUT_WR)
            frames = Frames(right)
            self.assertEqual(frames.read(), {'text': '中文', 'n': 1})
            self.assertEqual(frames.read(), {'n': 2})
            self.assertIsNone(frames.read())
        finally: left.close(); right.close()

    def test_no_network_for_an_unused_scope(self):
        with patch('cosmesh.connections.subprocess.Popen', side_effect=AssertionError('Unexpected process')):
            with session() as pool: self.assertEqual(pool.stats['rpc_requests'], 0)

    def test_control_reuse_is_private_and_does_not_write_user_config(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as folder:
            fake = subprocess.CompletedProcess([], 0, b'hostname verified\nuser alice\nport 22\n', b'')
            with patch('cosmesh.connections.subprocess.run', return_value=fake) as run:
                with session():
                    first = control_options(['ssh', '-T', 'verified'])
                    second = control_options(['ssh', '-T', 'verified'])
                self.assertEqual(first, second); self.assertEqual(run.call_count, 1)
                self.assertIn('ControlPersist=120', first)
                target = Path(first[-1].split('=', 1)[1])
                self.assertEqual(target.parent.stat().st_uid, os.geteuid())
                self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
                changed = control_options(['ssh', '-T', 'another-alias'])
                self.assertNotEqual(first[-1], changed[-1])


class ContinuousTests(unittest.TestCase):
    setUp = fixtures.SystemAccessTests.setUp
    start = fixtures.SystemAccessTests.start
    stop = fixtures.SystemAccessTests.stop

    def test_many_ssh_rpc_frames_use_one_process_and_verified_identity(self):
        self.hub.initialize_community(self.folder/'library')
        raw = b'frame-data-'*200000
        sha = self.hub.community().save_blob(raw)
        original = subprocess.Popen; launches = []
        def local_ssh(arguments, **kwargs):
            if arguments[0] == 'ssh':
                launches.append(arguments); arguments = shlex.split(arguments[-1])
            return original(arguments, **kwargs)
        config = {'kind':'ssh-system','host':'isolated-ssh','discovery':str(self.discovery),'repo':'spaces'}
        data = bytearray()
        with patch('cosmesh.sync.control_options', return_value=[]), patch('cosmesh.connections.subprocess.Popen', side_effect=local_ssh):
            with session() as pool:
                while len(data) < len(raw):
                    reply = spaces.rpc(config, {'method':'community_get','sha256':sha,'offset':len(data)})
                    self.assertEqual(reply['actor'], self.user); self.assertEqual(reply['uid'], os.geteuid())
                    data.extend(base64.b64decode(reply['data']))
                self.assertGreater(pool.stats['rpc_requests'], 8)
                self.assertEqual(pool.stats['ssh_rpc_processes'], 1)
        self.assertEqual(bytes(data), raw); self.assertEqual(len(launches), 1)

    def test_batch_versions_skip_unchanged_fetch_and_keep_drafts_local(self):
        for i, name in enumerate(('alice','bob'), 1):
            public='ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+bytes([i])*32).decode()
            self.hub.register_key(name,public,self.entry)
            self.hub.spaces({'method':'ensure_threads'}, name)
        root = self.folder/'thread-work'; initialize(root, self.user); wb = Workbench(root)
        with wb.base.locked():
            wb.load(); wb.discover(service=str(self.discovery))
            work = wb.create('batch-work')['id']; wb.load(); wb.share(work); wb.synchronize()
            record = wb.append(work, 'private-pending')['id']; wb.load()
            original = Store.git; fetches=[]
            def counted(store, *args, **kwargs):
                if args[0]=='fetch': fetches.append(args)
                return original(store, *args, **kwargs)
            with patch('cosmesh.store.Store.git', counted), patch('cosmesh.sync.remote_status', side_effect=AssertionError('Unbatched status')):
                with session() as pool:
                    result = wb.synchronize(publish=False)
                    self.assertTrue(result['refreshed'], result)
                    self.assertEqual(pool.stats['rpc_requests'], 1)
                    self.assertEqual(pool.stats['local_channels'], 1)
            self.assertEqual(fetches, [])
            self.assertFalse(wb.accepted(wb.find(record)))

    def test_existing_blob_stops_upload_after_first_acknowledgement(self):
        self.hub.initialize_community(self.folder/'library')
        self.hub.spaces({'method':'community_name','name':'隔离连接验收','key':'name'}, self.user)
        root = self.folder/'thread-work'; initialize(root, self.user); wb=Workbench(root)
        with wb.base.locked():
            wb.load(); wb.discover(service=str(self.discovery)); kb=Knowledge(wb)
            raw=b'uploaded-'*200000; sha=kb.cache_blob(raw)
            with session() as pool:
                kb.upload(sha)
                self.assertGreater(pool.stats['rpc_requests'], 1)
                self.assertEqual(pool.stats['local_channels'], 1)
            with session() as pool:
                kb.upload(sha)
                self.assertEqual(pool.stats['rpc_requests'], 1)
            self.assertEqual(self.hub.community().blob(sha).read_bytes(), raw)

    def test_relay_v2_rechecks_account_for_each_frame(self):
        public = 'ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'r'*32).decode()
        key = self.hub.register_key(self.user, public, self.entry)['key_id']
        path = self.hub.root/'policy/keys.json'; keys=json.loads(path.read_text()); keys[key]['relay']=True
        path.write_text(json.dumps(keys))
        env=dict(os.environ,SSH_CONNECTION='isolated-relay',SSH_ORIGINAL_COMMAND='cosmesh-relay v2')
        process=subprocess.Popen([str(self.entry),'hub','serve','--hub-root',str(self.hub.root),'--key-id',key],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        try:
            reader=Frames(process.stdout);hello=reader.read()
            self.assertEqual(hello['relay_stream'],2)
            value={'protocol':1,'repo':'team','operation':'cosmesh-status'}
            frame={'uid':os.geteuid(),'user':self.user,'request':value,'endpoint':hello['endpoint']}
            for _ in range(2):
                send_frame(process.stdin,frame);reply=reader.read()
                self.assertTrue(reply['ok']);self.assertEqual(reply['data']['uid'],os.geteuid())
            send_frame(process.stdin,{**frame,'user':'forged-account'})
            self.assertEqual(reader.read()['error']['code'],'AUTH_DENIED')
            process.wait(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdin.close();process.stdout.close();process.stderr.close()

    def test_changed_reply_identity_is_rejected_without_replaying_write(self):
        original=subprocess.Popen;starts=[]
        marker=self.folder/'requests.jsonl'
        script=('import json,os,pwd,sys\n'
                'print(json.dumps({"stream":1,"actor":pwd.getpwuid(os.geteuid()).pw_name,"uid":os.geteuid(),"endpoint":"isolated"}),flush=True)\n'
                'request=json.loads(sys.stdin.readline())\n'
                'open(%r,"a").write(json.dumps(request)+"\\n")\n'
                'print(json.dumps({"id":request["id"],"reply":{"ok":True,"endpoint":"isolated","data":{"actor":pwd.getpwuid(os.geteuid()).pw_name,"uid":os.geteuid()+1}}}),flush=True)\n') % str(marker)
        def fake_ssh(arguments,**kwargs):
            if arguments[0]=='ssh':
                starts.append(arguments);arguments=[os.sys.executable,'-c',script]
            return original(arguments,**kwargs)
        config={'kind':'ssh-system','host':'isolated','discovery':'/isolated/service.json','repo':'spaces'}
        with patch('cosmesh.sync.control_options',return_value=[]),patch('cosmesh.connections.subprocess.Popen',side_effect=fake_ssh):
            with session() as pool:
                with self.assertRaises(Error) as caught:
                    spaces.rpc(config,{'method':'community_name','name':'隔离写入','key':'same-write'})
                self.assertEqual(caught.exception.code,'AUTH_DENIED')
                self.assertEqual(pool.stats['rpc_requests'],1)
        self.assertEqual(len(starts),1)
        self.assertEqual(len(marker.read_text().splitlines()),1)

    def test_revoked_relay_key_is_rechecked_on_an_open_stream(self):
        public='ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'v'*32).decode()
        key=self.hub.register_key(self.user,public,self.entry)['key_id']
        path=self.hub.root/'policy/keys.json';keys=json.loads(path.read_text());keys[key]['relay']=True
        path.write_text(json.dumps(keys))
        env=dict(os.environ,SSH_CONNECTION='isolated-relay',SSH_ORIGINAL_COMMAND='cosmesh-relay v2')
        process=subprocess.Popen([str(self.entry),'hub','serve','--hub-root',str(self.hub.root),'--key-id',key],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        try:
            reader=Frames(process.stdout);hello=reader.read()
            frame={'uid':os.geteuid(),'user':self.user,'endpoint':hello['endpoint'],
                   'request':{'protocol':1,'repo':'team','operation':'cosmesh-status'}}
            send_frame(process.stdin,frame);self.assertTrue(reader.read()['ok'])
            keys[key]['enabled']=False;path.write_text(json.dumps(keys))
            send_frame(process.stdin,frame)
            self.assertEqual(reader.read()['error']['code'],'AUTH_DENIED')
            process.wait(timeout=5)
        finally:
            if process.poll()is None:process.kill();process.wait()
            process.stdin.close();process.stdout.close();process.stderr.close()
