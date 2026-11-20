"""Portable working files and the boundary between personal and shared facts."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_thread_personal as fixture
from tests.test_threads import ThreadFixture
from cosmesh.errors import Error
from cosmesh.personal import Personal, initialize
from cosmesh.personal_protocol import save_json
from cosmesh.personal_relay import initialize as enable
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.thread_personal_protocol import validate_manifest


class FileContinuationTests(unittest.TestCase):
    setUp = fixture.ThreadPersonalTests.setUp
    work = fixture.ThreadPersonalTests.work
    file = fixture.ThreadPersonalTests.file
    use = fixture.ThreadPersonalTests.use

    def test_verified_files_skip_content_and_staging_but_detect_replacements(self):
        from cosmesh.thread_personal_cli import brief_receipt

        path = self.file('sample.bin', b'original')
        with self.use(self.a) as p:
            aid = p.add(self.t, path)['id']
            first = p.synchronize(self.t, files=True)
            self.assertEqual(brief_receipt(first)['files']['updated'], 1)
            blob = p.asset(aid)['blob']
            from cosmesh.personal_relay import Relay
            hub_file = Relay(self.hub, 'alice').blobs / blob
            p.wb.append(self.t, 'Metadata-only change'); p.wb.load()
            real_open, real_rpc = Path.open, p.rpc

            def forbid_source_or_stage(opened, *args, **kwargs):
                if opened in (path, hub_file) or opened.parent == p.staging:
                    self.fail('Reused file was opened or staged')
                return real_open(opened, *args, **kwargs)

            def forbid_file_rpc(payload):
                if payload.get('sha') == blob:
                    self.fail('Current Thread manifest already confirms this file')
                return real_rpc(payload)

            with patch.object(Path, 'open', forbid_source_or_stage), patch.object(p,'rpc',side_effect=forbid_file_rpc):
                again = p.synchronize(self.t, files=True)
            self.assertEqual(brief_receipt(again), {'thread':self.t, 'status':'synced', 'complete':True,
                             'files':{'updated':0,'unchanged':1,'skipped':0}})
            # Same size and preserved mtime, different inode and bytes.
            import os
            st = path.stat()
            replacement = self.file('replacement.bin', b'modified')
            os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
            replacement.replace(path)
            result = p.synchronize(self.t, files=True)
            self.assertEqual(brief_receipt(result)['files']['updated'], 1)
            self.assertNotEqual(p.asset(aid)['blob'], blob)

            # A rolled-back client can discard the optional local proof.
            p.state['asset_copies'][aid].pop('fingerprint')
            p.save()
            with patch.object(p, 'upload', wraps=p.upload) as upload:
                self.assertEqual(brief_receipt(p.synchronize(self.t, files=True))['files']['unchanged'], 1)
                self.assertTrue(any(call.args[0].parent == p.staging for call in upload.call_args_list))
            self.assertEqual(brief_receipt(p.synchronize(self.t, files=True))['files']['unchanged'], 1)

        with self.use(self.b) as p:
            target = self.root / 'restored'
            result = p.restore(self.t, str(target), 'test', files=True)
            self.assertTrue(brief_receipt(result)['complete'])
            self.assertEqual(brief_receipt(p.synchronize(self.t, files=True))['files']['unchanged'], 1)
            (target / 'sample.bin').write_bytes(b'local changed')
            result = p.restore(self.t, str(target), 'test', files=True)
            self.assertEqual(brief_receipt(result)['status'], 'partial')
            self.assertEqual(brief_receipt(result)['issues'][0]['reason'], 'existing_destination')
            self.assertEqual((target / 'sample.bin').read_bytes(), b'local changed')

    def test_file_changed_while_reusing_receipt_does_not_commit(self):
        path = self.file()
        with self.use(self.a) as p:
            aid = p.add(self.t, path)['id']
            base = p.synchronize(self.t, files=True)['accepted_snapshot']
            plan = p.plan
            def race(*args, **kwargs):
                rows = plan(*args, **kwargs)
                self.assertTrue(rows['files'][0]['unchanged'])
                path.write_bytes(b'new local work')
                return rows
            with patch.object(p, 'plan', side_effect=race), self.assertRaises(Error) as exc:
                p.synchronize(self.t, files=True)
            self.assertEqual(exc.exception.code, 'STATE_CONFLICT')
            self.assertEqual(p.rpc({'method':'personal_thread_head','thread':self.t})['sha'], base)
            p.synchronize(self.t, files=True)
            self.assertEqual(p.asset(aid)['size'], len(b'new local work'))

    def test_restore_run_edit_and_sync_same_resource_without_overwriting_source(self):
        source = self.file('run.py', b'print(6 * 7)\n')
        with self.use(self.a) as p:
            aid = p.add(self.t, source, relative_path='code/run.py')['id']
            huge = self.root / 'cube.fits'
            with huge.open('wb') as f: f.truncate(100 * 1024**3)
            p.add(self.t, huge)
            p.synchronize(self.t, files=True)
        destination = self.root / 'new-project'
        with self.use(self.b) as p:
            result = p.restore(self.t, str(destination), 'dsh', files=True)
            self.assertEqual(result['restoration']['files'], 'partial')
            self.assertIn('file_limit', [f['reason'] for f in result['files']['files']])
            script = destination / 'code/run.py'
            self.assertEqual(subprocess.check_output([sys.executable, str(script)], text=True).strip(), '42')
            script.write_text('print(7 * 8)\n')
            self.assertEqual(p.open(aid, True)['path'], str(script))
            p.synchronize(self.t, files=True)
            again = p.restore(self.t, str(destination), 'dsh', files=True)
            self.assertTrue(next(f for f in again['files']['files'] if f['id'] == aid)['unchanged'])
            self.assertEqual(len(p.wb.client['bindings']), 1)
        with self.use(self.a) as p:
            result = p.synchronize(self.t, files=True)
            self.assertIn('LOCAL_FILE_DIVERGED', [r['reason'] for r in result['transfers']['files']])
            self.assertEqual(source.read_bytes(), b'print(6 * 7)\n')
            self.assertNotEqual(p.open(aid, True)['base_sha'], p.asset(aid)['blob'])
            self.assertEqual(p.asset(aid)['thread'], self.t)

    def test_explicit_scope_and_no_files_without_flag(self):
        with self.use(self.a) as p:
            p.add(self.t, self.file(), relative_path='tables/result.csv');p.synchronize(self.t,files=True)
        target = self.root/'bound-only'
        with self.use(self.b) as p:
            result = p.restore(self.t,str(target),'codex')
            self.assertEqual(result['restoration']['files'],'not_requested')
            self.assertFalse((target/'tables/result.csv').exists())
            with patch.object(p.wb.base,'git',side_effect=AssertionError('locations must not traverse history')):
                self.assertTrue(p.locations(self.t)['locations'])

    def test_existing_files_symlinks_and_unsafe_paths_are_preserved(self):
        with self.use(self.a) as p:
            file=self.file('report.txt',b'accepted report')
            for value in ('../escape','/absolute','.ssh/key','folder/../../escape'):
                with self.assertRaises(Error):p.add(self.t,file,relative_path=value)
            aid=p.add(self.t,file,relative_path='reports/report.txt')['id'];p.synchronize(self.t,files=True)
            manifest=p.manifest(self.t);manifest['assets'][aid]['relative_path']='../escape'
            with self.assertRaises(Error):validate_manifest(manifest,p.wb.base)
        target=self.root/'target';(target/'reports').mkdir(parents=True)
        local=target/'reports/report.txt';local.write_text('local work')
        with self.use(self.b) as p:
            result=p.restore(self.t,str(target),'codex',files=True)
            self.assertEqual(result['files']['files'][0]['state'],'conflict')
            self.assertEqual(local.read_text(),'local work')
            alternate=self.root/'alternate';alternate.mkdir()
            (alternate/'reports').symlink_to(target/'reports',target_is_directory=True)
            result=p.restore(self.t,str(alternate),'codex',files=True)
            self.assertEqual(result['files']['files'][0]['reason'],'PATH_INVALID')
            self.assertEqual(local.read_text(),'local work')

    def test_failed_file_download_retries_without_duplicate_thread(self):
        with self.use(self.a) as p:
            aid=p.add(self.t,self.file())['id'];p.synchronize(self.t,files=True)
            sha=p.asset(aid)['blob']
        with self.use(self.b) as p:
            real=p.rpc
            def broken(payload):
                if payload['method']=='personal_get' and payload['sha']==sha:raise Error('SYNC_NETWORK','interrupted')
                return real(payload)
            target=self.root/'retry-target'
            with patch.object(p,'rpc',side_effect=broken):
                result=p.restore(self.t,str(target),'dsh',files=True)
            self.assertEqual(result['files']['state'],'partial')
            self.assertEqual(result['files']['files'][0]['reason'],'SYNC_NETWORK')
            result=p.restore(self.t,str(target),'dsh',files=True)
            self.assertEqual(result['files']['state'],'complete')
            self.assertEqual(len(p.wb.catalog),1)

    def test_new_transport_cannot_run_legacy_whole_store_recovery(self):
        with self.use(self.a) as p:
            for name in ('sync','snapshot','join','_apply'):self.assertFalse(hasattr(p,name))
            before=p.wb.base.head
            save_json(p.root/'apply.json',{'old_journal':'preserve'})
            with self.assertRaises(Error) as exc:ThreadPersonal(p.wb)
            self.assertEqual(exc.exception.code,'LEGACY_RECOVERY_REQUIRED')
            self.assertEqual(p.wb.base.head,before)
            self.assertTrue((p.root/'apply.json').exists())


class SharedThreadTests(ThreadFixture):
    participants=('alice','bob')

    def test_restored_owner_publishes_same_id_and_peer_keeps_private_execution(self):
        enable(self.hub)
        a=self.workbench('alice','source','local-a');b=self.workbench('alice','destination','local-b')
        def personal(wb):
            initialize(wb,uuid4().hex);p=ThreadPersonal(wb)
            p.state.update(connection=self.connection('alice'),actor='alice');p.save();return p
        with self.use(a):
            tid=a.create('One shared work','shared facts')['id'];a.load()
            p=personal(a);p.synchronize(tid)
        with self.use(b):
            p=personal(b);p.restore(tid,str(self.root),'codex',session_id='owner-private-session')
            b.share(tid);result=b.synchronize();self.assertTrue(result['synchronized'],result)
            b.load();self.assertEqual(b.find(tid)['repo'],self.repos['alice'])
        self.update('bob')
        with self.use('bob') as peer:
            peer.bind(tid,'dsh',str(self.root),session_id='peer-private-session')
            request=peer.append(tid,'review this',record_type='request',recipient='alice');peer.load()
            result=peer.synchronize();self.assertTrue(result['synchronized'],result)
            # Contributions and requests stay on the original Thread, with Bob's authorship.
            self.assertEqual(peer.find(request['id'])['doc']['meta']['author'],'bob')
            self.assertEqual(peer.find(request['id'])['doc']['meta']['thread'],tid)
            initialize(peer,uuid4().hex);p=ThreadPersonal(peer)
            self.assertTrue(p.locations(tid)['locations'])
            with self.assertRaises(Error):peer.edit(tid,peer.ref(peer.find(tid))['version'],body='replace owner overview')
        server=self.hub.repo(self.repos['alice'])
        history=server.git('log','-p','main')
        self.assertNotIn('owner-private-session',history);self.assertNotIn('peer-private-session',history)

    def test_local_username_collision_does_not_grant_public_ownership(self):
        tid=self.shared('alice','Alice work')
        bob=self.workbench('bob','bob-with-alice-local-name','alice')
        with self.use(bob):
            self.assertEqual(bob.owned_authors(bob.find(tid)),{'bob'})
            from cosmesh.cli import parser
            from cosmesh.thread_cli import execute
            before=bob.base.head
            args=parser('cosmesh').parse_args(['thread','public-branch',tid,'--title','retired','--root',str(bob.base.root)])
            with self.assertRaises(Error) as exc:execute(args)
            self.assertEqual(exc.exception.code,'COMMAND_RETIRED');self.assertEqual(bob.base.head,before)
