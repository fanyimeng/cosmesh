"""Optional empty bodies retain nonempty titles and the existing record contract."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.thread_chat import with_summary
from cosmesh.threads import Workbench, initialize


class EntryTitleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)/'work'
        initialize(self.root,'title-test');self.wb=Workbench(self.root)
        env=patch.dict('os.environ',{'COSMESH_PAGE_BACKGROUND':'0'})
        env.start();self.addCleanup(env.stop)
        with self.wb.base.locked():
            self.wb.load();self.tid=self.wb.create('原工作标题','原目标')['id']

    def test_empty_and_whitespace_state_bodies_use_fallback(self):
        with self.wb.base.locked():
            for state,body in [('done',''),('active','  \n\t')]:
                self.wb.load();result=self.wb.append(self.tid,body,record_type='state',state=state)
                self.wb.load();doc=self.wb.find(result['id'])['doc']
                self.assertEqual(doc['body'],'')  # Existing Markdown codec normalizes whitespace-only bodies.
                self.assertEqual(doc['meta']['title'],'state')
            self.assertEqual(self.wb.find(self.tid)['doc']['meta']['title'],'原工作标题')

    def test_empty_record_retry_preserves_exact_id_and_version(self):
        with self.wb.base.locked():
            self.wb.load();first=self.wb.append(self.tid,record_type='state',state='done',key='once')
            self.wb.load();again=self.wb.append(self.tid,record_type='state',state='done',key='once')
            self.assertTrue(again['replayed']);self.assertEqual(first['id'],again['id'])
            self.assertEqual(first['version'],again['version'])
            self.assertEqual(len(self.wb.records(self.tid)),1)

    def test_existing_title_precedence_and_no_truncation(self):
        cases=[('',None,'记录'),('普通首行\n更多文字',None,'普通首行'),
               ('x'*101,None,'记录'),(with_summary('正文','作者摘要'),None,'记录'),
               ('','明确指定','明确指定')]
        with self.wb.base.locked():
            for body,title,expected in cases:
                self.wb.load();result=self.wb.append(self.tid,body,title=title)
                self.wb.load();doc=self.wb.find(result['id'])['doc']
                self.assertEqual(doc['meta']['title'],expected)
                self.assertEqual(doc['body'],body)

    def test_explicit_blank_title_still_rejected(self):
        with self.wb.base.locked():
            self.wb.load();before=self.wb.base.head
            with self.assertRaises(Error) as error:self.wb.append(self.tid,'',title=' ')
            self.assertEqual(error.exception.code,'INPUT_INVALID')
            self.assertEqual(before,self.wb.base.head)

    def test_cli_state_archive_restore_without_body(self):
        for args in [('state','--to','done'),('restore',),('archive',),('restore',)]:
            output=io.StringIO()
            with redirect_stdout(output):
                code=main(['thread',args[0],self.tid,*args[1:],'--root',str(self.root),'--json'])
            reply=json.loads(output.getvalue());self.assertEqual(code,0,reply)
            self.assertTrue(reply['ok'],reply)
        with self.wb.base.locked():
            self.wb.load()
            self.assertTrue(all(row['doc']['meta']['title'].strip() for row in self.wb.records(self.tid)))


if __name__=='__main__':unittest.main()
