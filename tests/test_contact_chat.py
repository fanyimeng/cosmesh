"""Five true rounds, stable older windows, authors, dates and public scope."""
import json
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cosmesh.contact_chat import read
from cosmesh.errors import Error
from cosmesh.status_pages import database
from cosmesh.thread_chat import document


class ContactViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.identity={'actor':'alice','local':'local-alice','timezone':'Asia/Shanghai'}
        self.users=[{'account':u,'name':n,'id':u} for u,n in [('alice','张三'),('bob','李四'),('carol','王五')]]
        self.fresh={'generated_at':'2026-11-04T07:00:00+00:00','remote_checked_at':None,'local_sources_current':True}
        self.con_context=database(self.root);self.con=self.con_context.__enter__();self.addCleanup(self.con_context.__exit__,None,None,None)
        self.thread='W-'+str(uuid4())
        self.card={'id':self.thread,'title':'云研究','naming':{}}
        self.con.execute('INSERT INTO threads (id,repo,card) VALUES (?,?,?)',(self.thread,'w-example',json.dumps(self.card)))
        for index in range(8):
            q=self.add('alice','request',index*3,body='请求'+str(index))
            self.add('alice','reply',index*3+1,request=q,body='连续补充'+str(index))
            self.add('bob','reply',index*3+2,request=q,body='回应'+str(index))
        self.con.commit()

    def add(self,author,kind,minute,request=None,body='补充',private=False):
        identifier='E-'+str(uuid4());source={'repo':'w-example','id':identifier,'version':'a'*64}
        entry={'id':identifier,'author':author,'author_name':'张三' if author=='alice' else '李四','kind':kind,
               'created_at':'2026-11-04T06:%02d:00+00:00'%minute,'source':source,'accepted':True,
               'interaction_summary':{'text':body,'origin':'author','truncated':False},'superseded_by':[],
               'refs':[{'relation':'request','source':request}] if request else []}
        if kind=='request':entry.update(to='bob',request_status={'state':'pending','confirmed':True,'response_count':1})
        self.con.execute('INSERT INTO entries VALUES (?,?,?,?,?)',(self.thread,identifier,kind,entry['created_at'],json.dumps(entry)))
        self.con.execute('INSERT INTO entry_meta VALUES (?,?,?,?,?)',(identifier,self.thread,author,request['id'] if request else None,entry['created_at']))
        return source

    def read(self,**kwargs):
        args=SimpleNamespace(contact='李四',rounds=5,before=None,date=None,include_progress=False,**{})
        for k,v in kwargs.items():setattr(args,k,v)
        return read(self.con,args,self.identity,self.users,self.fresh)

    def test_five_rounds_merge_sources_and_continue_without_duplicates_after_new_message(self):
        page=self.read();self.assertEqual(len(page['rounds']),5);self.assertEqual(len(page['messages']),10)
        self.assertTrue(all(len(m['parts'])==2 for m in page['messages'] if m['author']=='alice'))
        self.assertTrue(all(m['local_time'].startswith('2026-11-04 14:') for m in page['messages']))
        first_ids={p['id'] for m in page['messages'] for p in m['parts']}
        self.add('alice','request',30,body='后来新增但不混入旧窗口');self.con.commit()
        older=self.read(before=page['next_cursor'])
        self.assertEqual(len(older['rounds']),3);self.assertIsNone(older['next_cursor'])
        older_ids={p['id'] for m in older['messages'] for p in m['parts']}
        self.assertFalse(first_ids & older_ids)
        self.assertNotIn('后来新增',json.dumps(older,ensure_ascii=False))
        self.assertEqual(older['freshness'],page['freshness'])
        latest=self.read();self.assertEqual(latest['total_rounds'],9);self.assertTrue(latest['rounds'][-1]['open'])
        self.assertIn('🔴',document(latest));self.assertIn('🔵',document(latest))

    def test_filters_scope_progress_and_cursor_validation(self):
        self.add('bob','note',25,body='只在显式选择时纳入的共享进展');self.con.commit()
        self.assertNotIn('共享进展',json.dumps(self.read(),ensure_ascii=False))
        expanded=self.read(include_progress=True)
        self.assertTrue(any(m['shared_progress'] for m in expanded['messages']))
        self.assertIn('共享进展',document(expanded))
        self.assertEqual(self.read(date='2026-11-03')['total_rounds'],0)
        for kwargs in ({'date':'bad'},{'before':'invalid'},{'contact':'alice'},{'rounds':0}):
            with self.assertRaises(Error):self.read(**kwargs)
        page=self.read()
        with self.assertRaises(Error):self.read(contact='carol',before=page['next_cursor'])
        self.con.execute('UPDATE threads SET repo=?',('local',));self.con.commit()
        self.assertEqual(self.read()['total_rounds'],0)

    def test_cached_window_never_reloads_entry_rows(self):
        self.fresh['source_token']='same-committed-sources'
        first=self.read()
        def authorize(action,table,*rest):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table in {'entries','threads'} else sqlite3.SQLITE_OK
        self.con.set_authorizer(authorize)
        try:second=self.read()
        finally:self.con.set_authorizer(None)
        self.assertEqual(first,second)
