"""Necessary migration, access inheritance and broad transfer closures."""
import base64
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from tests.test_thread_tracking import TrackingTests
from cosmesh.errors import Error
from cosmesh.knowledge import Knowledge
from cosmesh.thread_migration import migrate
from cosmesh.personal_protocol import packed, digest, inspect_file
from cosmesh.thread_access_cli import Online
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.personal import initialize
from cosmesh.threads import Workbench, initialize as init_work


class MigrationTests(TrackingTests):
    def legacy_resource(self,tid):
        with self.use('alice') as wb:
            kb=Knowledge(wb);kb.refresh()
            f=self.root/'source.txt';f.write_text('shared original content')
            resource={'id':'K-'+str(uuid4()),'title':'inherited report','body':'source detail',
                      'files':kb.import_files([f]),'refs':[wb.ref(wb.find(tid))]}
            for file in resource['files']:
                for sha in (file['sha256'],file['text_sha256']):
                    if sha:kb.upload(sha)
            result=kb.apply('resource_add',resource,'create-resource');kb.refresh()
            return result,resource['files'][0]

    def test_migration_acl_library_and_old_api_fence(self):
        tid=self.shared('alice','legacy work','all original content')
        resource,file=self.legacy_resource(tid)
        before={p:p.read_bytes() for p in (self.hub.repo(self.repos['alice']).root/'objects').rglob('*') if p.is_file()}
        planned=migrate(self.hub);self.assertEqual(len(planned['threads']),1)
        result=migrate(self.hub,True,True);self.assertTrue(Path(result['backup'],'sources.tar').is_file())
        self.assertTrue(migrate(self.hub,True,True)['replayed'])
        self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))
        self.assertEqual(self.command('bob','browse',tid)['body'],'all original content')
        for method in ('list_threads','thread_chat_ready','thread_mail'):
            with self.assertRaises(Error):self.hub.spaces({'method':method},'carol')
        with self.assertRaises(Error):self.hub.policy(self.repos['alice'],'alice')
        old=self.hub.spaces({'method':'community_info'},'carol')
        self.grant(tid)
        self.assertEqual(self.command('bob','browse',tid)['body'],'all original content')
        with self.assertRaises(Error):self.command('carol','browse',tid)
        for who in ('bob','carol'):
            with self.use(who) as wb:
                kb=Knowledge(wb);kb.refresh()
                self.assertEqual(resource['id'] in kb.data['heads'],who=='bob')
            search=self.hub.spaces({'method':'community_search','query':'inherited','offset':0},who)
            self.assertEqual(bool(search['hits']),who=='bob')
        for sha in (file['sha256'],old['sha256']):
            with self.assertRaises(Error):self.hub.spaces({'method':'community_get','sha256':sha,'offset':0},'carol')
        # Knowing an object hash and its size is not upload ownership proof.
        result=self.hub.spaces({'method':'community_put','sha256':file['sha256'],'size':file['size'],'offset':0,'data':''},'carol')
        self.assertFalse(result['complete'])
        with self.assertRaises(Error):self.hub.spaces({'method':'community_get','sha256':file['sha256'],'offset':0},'carol')
        self.grant(tid,'private',())
        with self.assertRaises(Error):self.hub.spaces({'method':'community_get','sha256':file['sha256'],'offset':0},'bob')
        self.assertTrue((self.hub.community().blob(file['sha256'])).is_file())
        # The old fixed runtime must reject the format fence.
        import subprocess
        oldpy=Path('build/thread-tracking-0.4.5/install-checked/versions/0.4.3/runtime/bin/python').absolute()
        if oldpy.is_file():
            run=subprocess.run([str(oldpy),'-I','-c',"from cosmesh.hub import Hub;Hub(__import__('sys').argv[1]).settings()",str(self.hub.root)],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0);self.assertIn('Hub',run.stderr)

    def test_new_library_inherits_revisions_and_owner_recovers(self):
        tid=self.shared('alice','recover original','original')
        migrate(self.hub,True,True)
        root=self.root/'fresh-owner';init_work(root,'different-local-name');second=Workbench(root)
        with self.use(second):
            initialize(second);p=ThreadPersonal(second);p.state.update(connection=self.connection('alice'),actor='alice');p.save();p.restore(tid)
            self.assertEqual(second.find(tid)['doc']['meta']['author'],'alice')
            second.append(tid,'owner new note');second.load();p.synchronize(tid)
        self.command(second,'sync',tid)
        self.assertIn('owner new note',packed(self.command('bob','browse',tid,'--records')))
        self.grant(tid)
        with self.use('alice') as wb:
            kb=Knowledge(wb);kb.refresh();f=self.root/'data.weird';f.write_bytes(b'\x00\xffcompressed or science data')
            files=kb.import_files([f])
            for ff in files:kb.upload(ff['sha256'])
            value={'id':'K-'+str(uuid4()),'title':'new scoped file','body':'private report','files':files,'source_threads':[tid],
                   'refs':[{'repo':'access','id':tid,'version':self.hub.spaces({'method':'thread_access_read','thread':tid,'revision':None,'record':None,'version':None,'offset':0,'chars':1},'alice')['version']}]}
            first=kb.apply('resource_add',value,'new-file');kb.refresh()
            revised=kb.apply('resource_revise',{'id':first['id'],'expect':first['version'],'body':'revised report'},'revise-file')
        with self.use('bob') as wb:
            kb=Knowledge(wb);kb.refresh();self.assertEqual(len(kb.data['versions']),2)
        self.grant(tid,'private',())
        with self.use('bob') as wb:
            kb=Knowledge(wb);kb.refresh();self.assertEqual(kb.data['versions'],[])

    def test_migration_resume_and_broad_file_policy(self):
        tid=self.shared('alice','resume work','kept')
        with patch('cosmesh.thread_migration.put',side_effect=Error('IO_TEST','interrupted')),self.assertRaises(Error):migrate(self.hub,True,True)
        with self.assertRaises(Error):self.command('bob','browse',tid)
        result=migrate(self.hub,True,True);self.assertEqual(result['thread_count'],1)
        from cosmesh.transfer_policy import PERSONAL,PUBLIC,effective,PREVIOUS
        self.assertEqual(effective(PREVIOUS),PERSONAL);self.assertEqual(effective({**PREVIOUS,'auto_file':7})['auto_file'],7)
        for name in ('data.fits','data.h5','data.npz','data.zip','unknown.foo','program.bin'):
            p=self.root/name;p.write_bytes(b'\x00\xffopaque')
            self.assertIsNone(inspect_file(p,PERSONAL['auto_file'])['reason'])
        p=self.root/'oversize.bin'
        with p.open('wb') as f:f.truncate(PERSONAL['auto_file']+1)
        self.assertEqual(inspect_file(p,PERSONAL['auto_file'])['reason'],'file_limit')
        p=self.root/'id_rsa';p.write_text('private');self.assertIsNotNone(inspect_file(p,PERSONAL['auto_file'])['reason'])

    def test_existing_private_head_and_library_stream_preserved(self):
        tid=self.work('private then public','owner content')
        with self.use('alice') as wb:
            initialize(wb);p=ThreadPersonal(wb);p.state.update(connection=self.connection('alice'),actor='alice');p.save()
            asset=self.root/'private.data';asset.write_bytes(b'private original')
            p.add(tid,asset);p.synchronize(tid,files=True)
            old=p.rpc({'method':'personal_thread_head','thread':tid})['sha']
            wb.share(tid);self.assertTrue(wb.synchronize()['synchronized'])
            wb.bind(tid,'dsh',str(self.root),'retained-session');wb.load()
            from cosmesh.thread_execution import ThreadExecution
            ThreadExecution(wb).synchronize(tid)
        migrate(self.hub,True,True)
        with self.use('alice') as wb:
            p=ThreadPersonal(wb);head=p.rpc({'method':'personal_thread_head','thread':tid})['sha']
            value=p.load_manifest(head,tid);self.assertTrue(value['assets']);self.assertIn('retained-session',packed(value['bindings']))
            self.assertTrue(p.load_manifest(old,tid)['assets'])
        rows=self.command('alice','list','--personal','--remote')['threads']
        self.assertEqual(next(r for r in rows if r['thread']==tid)['personal_sync'],'thread')
        self.assertEqual(self.command('bob','browse',tid,'--files')['files'],[])
        from cosmesh.thread_access_cli import refresh_published
        with self.use('alice') as wb:
            protected=refresh_published(wb,ThreadPersonal(wb),tid,head)
            self.assertEqual(protected['state'],'review_required')
        self.assertEqual(self.command('bob','browse',tid,'--files')['files'],[])
        self.grant(tid)
        with self.use('alice') as wb:
            kb=Knowledge(wb);kb.refresh();large=self.root/'large.fits'
            with large.open('wb') as out:out.write(b'SIMPLE  =');out.truncate(21*1024*1024)
            files=kb.import_files([large]);self.assertEqual(files[0]['text_status'],'not_extracted')
            kb.upload(files[0]['sha256'])
            kid='K-'+str(uuid4());kb.apply('resource_add',{'id':kid,'title':'large dataset','files':files,'source_threads':[tid]},'stream-data')
        with self.use('bob') as wb:
            kb=Knowledge(wb);kb.refresh();result=kb.download_path(files[0]['sha256'],files[0]['size'])
            self.assertEqual(result.stat().st_size,large.stat().st_size)
            result.unlink();original=kb.rpc;chunks=0
            def revoke(payload):
                nonlocal chunks
                reply=original(payload)
                if payload['method']=='community_get':
                    chunks+=1
                    if chunks==1:self.grant(tid,'private',())
                return reply
            with patch.object(kb,'rpc',side_effect=revoke),self.assertRaises(Error):kb.download_path(files[0]['sha256'],files[0]['size'])
            self.assertFalse(result.exists());self.assertFalse(list(kb.root.glob('.download-*')))

    def test_legacy_archive_embedded_citations_inherit_source_access(self):
        source=self.shared('alice','source work','citation secret')
        target=self.shared('alice','archive work','public overview')
        with self.use('alice') as wb:
            wb.append(target,'cites source',refs=[wb.ref(wb.find(source))]);wb.load()
            wb.append(target,record_type='lifecycle',lifecycle='archived');wb.load();self.assertTrue(wb.synchronize()['synchronized'])
        migrate(self.hub,True,True)
        with self.hub.community().db() as db:
            archive=db.execute('SELECT asset FROM archives WHERE thread=?',(target,)).fetchone()['asset']
        self.grant(source)
        with self.use('carol') as wb:
            kb=Knowledge(wb);kb.refresh();self.assertNotIn(archive,kb.data['heads'])
        with self.use('bob') as wb:
            kb=Knowledge(wb);kb.refresh();self.assertIn(archive,kb.data['heads'])
