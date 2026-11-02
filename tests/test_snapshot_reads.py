"""Batch bytes, snapshot freshness and command-local reuse on actual Git stores."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.protocol import encode
from cosmesh.store import Store
from cosmesh.threads import Workbench, initialize
from cosmesh.thread_protocol import new_id, content_version


class SnapshotReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'work'; initialize(self.root, 'alice')
        self.wb = Workbench(self.root); self.wb.load()
        self.thread = {'meta': {'schema_version':2,'kind':'thread','id':new_id('thread'),
            'author':'alice','created_at':'2026-11-01T00:00:00+00:00','title':'中文工作'}, 'body':'正文\r\n\r\n末尾。\n\n'}
        self.path = 'documents/'+self.thread['meta']['id']+'.md'

    def shared(self):
        store = Store(self.root/'.cosmesh/mesh/test-space')
        store.initialize({'schema_version':2,'product':'cosmesh','user':'alice','timezone':'Asia/Shanghai','mode':'local_unverified','scope':'mesh'})
        store.commit({self.path:encode(self.thread)}, 'seed')
        store.save_client({'user':'alice','remote':{'kind':'system','repo':'test-space','discovery':'/synthetic-only'},'remote_revision':store.head})
        self.wb.client.update(actor='alice',own_repo='test-space',spaces={'test-space':{'owner':'alice'}})
        self.wb.save_client();self.wb.load()
        return store

    def test_batch_matches_text_git_show_and_uses_constant_processes(self):
        store=self.wb.base
        store.commit({self.path:encode(self.thread),'.cosmesh/operations/unicode.json':'{"text":"中文"}\r\n\n'},'seed')
        revision=store.head
        expected={p:store.git('show',revision+':'+p,check=False).stdout for p in
                  ('.gitignore','.cosmesh/config.json',self.path,'.cosmesh/operations/unicode.json')}
        original=Store.git;calls=[]
        def counted(at,*args,**kwargs):calls.append(args);return original(at,*args,**kwargs)
        with patch.object(Store,'git',counted):actual=store.snapshot(revision)[1]
        self.assertEqual(actual,expected)
        self.assertEqual(len(calls),3)
        self.assertFalse(any(c[0]=='show' for c in calls))
        tree=store.git('rev-parse',revision+'^{tree}')
        self.assertEqual(store.snapshot(tree,allow_tree=True)[1],expected)
        with self.assertRaises(Error):store.snapshot(tree)

    def test_batch_rejects_corrupt_truncated_and_extra_data(self):
        store=self.wb.base;store.commit({self.path:encode(self.thread)},'seed')
        original=Store.git
        for mode,code in (('changed','CHECKSUM_MISMATCH'),('truncated','GIT_FAILED'),('extra','GIT_FAILED')):
            def broken(at,*args,**kwargs):
                result=original(at,*args,**kwargs)
                if args==('cat-file','--batch'):
                    raw=result.stdout
                    if mode=='changed':
                        offset=raw.index(b'\n')+1;raw=raw[:offset]+bytes([raw[offset]^1])+raw[offset+1:]
                    elif mode=='truncated':raw=raw[:-2]
                    else:raw+=b'extra'
                    return subprocess.CompletedProcess(result.args,0,raw,b'')
                return result
            with self.subTest(mode=mode),patch.object(Store,'git',broken):
                with self.assertRaises(Error) as caught:store.snapshot()
                self.assertEqual(caught.exception.code,code)

    def test_snapshot_rejects_modes_and_paths_before_reading_blobs(self):
        store=self.wb.base;store.commit({self.path:encode(self.thread)},'seed')
        store.git('update-index','--chmod=+x',self.path);store.git('commit','-qm','invalid mode')
        with self.assertRaises(Error) as caught:store.snapshot()
        self.assertEqual(caught.exception.code,'PATH_INVALID')

    def test_unchanged_model_reuse_still_checks_paths_and_new_heads(self):
        store=self.shared();old=self.wb._models['test-space'][2]
        with patch('cosmesh.threads.Snapshot',side_effect=AssertionError('Unchanged source reparsed')):self.wb.load()
        location=store.path(self.path);text=location.read_text();location.unlink();location.symlink_to('/etc/hosts')
        try:
            with self.assertRaises(Error) as caught:self.wb.load()
            self.assertEqual(caught.exception.code,'PATH_INVALID')
        finally:location.unlink();location.write_text(text)
        changed={'meta':dict(self.thread['meta']),'body':'新版本'}
        store.commit({self.path:encode(changed)},'edit')
        self.wb.load();self.assertIsNot(old,self.wb._models['test-space'][2])
        self.assertEqual(self.wb.find(self.thread['meta']['id'])['doc']['body'],'新版本')

    def test_accepted_snapshot_and_historical_version_stay_distinct(self):
        store=self.shared();item=self.wb.find(self.thread['meta']['id']);oldref=self.wb.ref(item)
        self.assertTrue(self.wb.accepted(item))
        changed={'meta':dict(self.thread['meta']),'body':'本地待发布'}
        store.commit({self.path:encode(changed)},'edit');self.wb.load()
        item=self.wb.find(self.thread['meta']['id'])
        self.assertFalse(self.wb.accepted(item));self.assertTrue(self.wb._ref_accepted(oldref))
        self.assertEqual(content_version(self.wb.resolve(oldref)),oldref['version'])
        client=store.client();client['remote_revision']=store.head;store.save_client(client)
        self.wb.load();self.assertTrue(self.wb.accepted(self.wb.find(item['doc']['meta']['id'])))

    def test_read_cli_loads_workbench_once_and_write_receipt_is_current(self):
        original=Workbench.load;calls=[]
        def counted(wb):calls.append(wb);return original(wb)
        with patch.object(Workbench,'load',counted),contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['thread','list','--root',str(self.root),'--offline','--json'])
        self.assertEqual(code,0);self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(output.getvalue())['sync']['thread_count'],0)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['thread','create','--title','新工作','--root',str(self.root),'--offline','--json'])
        value=json.loads(output.getvalue());self.assertEqual(code,0)
        self.assertEqual(value['sync']['thread_count'],1)
        self.assertTrue(value['data']['saved_locally'])
