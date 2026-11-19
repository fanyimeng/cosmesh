"""Explicit access changes, online browsing and private person tracking."""
import base64
import hashlib
import os
from pathlib import Path
import tempfile

from .errors import Error
from .personal import initialize
from .spaces import connection
from .connections import networked
from .store import safe_path
from .thread_personal import ThreadPersonal
from .threads import Workbench
from .thread_tracking import attach, bindings, grouped


def rpc(config, payload):
    from .spaces import rpc as request
    return request(config, payload)


class Online:
    def __init__(self, wb):
        self.wb = wb
        self.config = wb.client.get('connection')
        if not self.config:
            from .personal_protocol import read_json
            state=read_json(wb.base.path('.cosmesh/personal-v1/state.json'), {})
            self.config=state.get('connection') or connection(wb.base.root)
        try:self.info=rpc(self.config, {'method':'thread_access_info'})
        except Error as exc:
            if exc.code in {'INPUT_INVALID','COMMAND_DENIED'}:
                raise Error('FEATURE_UNAVAILABLE','This Hub needs Thread access and online viewing support.')
            raise
        if self.info.get('protocol')!='thread.access.v1': raise Error('FEATURE_UNAVAILABLE','This Hub needs Thread access and online viewing support.')
        if wb.client.get('actor') and wb.client['actor'] != self.info['person']['account']:
            raise Error('AUTH_DENIED','Hub identity differs from this local client.')

    def call(self, method, **payload):
        reply=rpc(self.config, {'method':method, **payload})
        if reply.get('actor') != self.info.get('actor'):
            raise Error('AUTH_DENIED','Hub identity changed during this operation.')
        return reply

    def status(self, tid):
        try: return self.call('thread_access_status', thread=tid)
        except Error as exc:
            if exc.code=='NOT_FOUND':return None
            raise

    def direct(self, owner=None):
        rows=[]; offset=0
        while True:
            result=self.call('thread_access_list', direct=True, owner=owner, offset=offset, limit=100)
            rows.extend(result['threads'])
            if result['next_offset'] is None: return rows
            offset=result['next_offset']
            if offset>=10000: raise Error('INPUT_TOO_LARGE','Use an owner filter for this incoming-work list.')

    def publish(self, tid, mode, readers, files=False, expect=None, share_projects=False):
        if mode!='restricted' and readers:raise Error('INPUT_INVALID','A reader list applies only to restricted visibility.')
        if mode=='restricted' and not readers:mode='private'
        try:item=self.wb.find(tid,'thread');tid=item['doc']['meta']['id']
        except Error as exc:
            if exc.code!='NOT_FOUND':raise
            item=None
        old=self.status(tid)
        if old and not old['writable']:raise Error('AUTH_DENIED','Only the owner changes Thread access.')
        online_only=item is None or item['repo']!='local'
        if online_only and not old:
            raise Error('LEGACY_PUBLIC_MIGRATION_REQUIRED','Migrate the Hub before managing legacy Thread visibility.')
        if not online_only and item['doc']['meta']['author']!=self.wb.author_for(item):raise Error('AUTH_DENIED','Only the owner changes Thread access.')
        if online_only and files:raise Error('OWNER_RESTORE_REQUIRED','Recover your Thread before adding local files.')
        revision=old['acl_revision'] if old else 0
        if expect is not None and expect != revision:raise Error('STATE_CONFLICT','Thread access changed; review current access.')
        options = {}
        if share_projects:
            from .thread_execution_projection import SHARED_FORMAT, shared_projection
            from .thread_execution_reader import raw_locations
            if mode == 'private' or online_only:
                raise Error('INPUT_INVALID', 'Approve a Project summary from the restored owner Thread with reader access.')
            if self.info.get('project_summary_protocol') != SHARED_FORMAT:
                raise Error('FEATURE_UNAVAILABLE', 'This Hub cannot preserve approved Project summaries.')
            summary = shared_projection(raw_locations(self.wb, tid))
            revision_id = summary['shared_revision']
            options['approve_projects'] = revision_id
            if (old or {}).get('execution_metadata', {}).get('shared_revision') != revision_id:
                # A normal private receipt gives the approval its own immutable snapshot,
                # even when all records and files are identical to a legacy publication.
                payload = {'thread': tid, 'acl_revision': revision, 'summary': revision_id}
                self.wb._commit(self.wb.base, 'approve-projects', payload,
                                lambda: ({}, {'id': tid, 'approved_projects': revision_id}),
                                f'approve-projects-{tid}-{revision}-{revision_id}')
                self.wb.load()
        if mode=='private' and not old:return {'thread':tid,'visibility':'private','unchanged':True,'accepted':True}
        if mode=='private' or online_only:sha=old['content_revision']
        else:
            initialize(self.wb);personal=ThreadPersonal(self.wb)
            if personal.state.get('connection') not in (None,self.config) or personal.state.get('actor') not in (None,self.info['person']['account']):
                raise Error('AUTH_DENIED','Personal content and access must use the same Hub identity.')
            personal.state.update(connection=self.config,actor=self.info['person']['account']);personal.save()
            sha=personal.synchronize(tid, files=files)['accepted_snapshot']
        result=self.call('thread_access_set', thread=tid,sha=sha,visibility=mode,readers=readers,expect=revision,**options)
        self.wb.client.setdefault('thread_access',{})[tid]={'hub_id':self.info['hub_id'],'visibility':result['visibility'],
                'acl_revision':result['acl_revision']}
        self.wb.save_client()
        return result

    def download(self, args):
        if not args.id or not args.output:raise Error('INPUT_INVALID','A file download requires a Thread ID and explicit --output destination.')
        target=safe_path(args.output)
        if target.exists():raise Error('STATE_CONFLICT','The destination exists; choose a new path to preserve it.')
        if not target.parent.is_dir():raise Error('PATH_INVALID','Choose an existing destination directory.')
        fd,tmp=tempfile.mkstemp(prefix='.cosmesh-download-',dir=str(target.parent))
        offset=0; digest=hashlib.sha256(); expected=None
        try:
            with os.fdopen(fd,'wb') as out:
                while True:
                    reply=self.call('thread_access_file',thread=args.id,revision=args.snapshot,asset=args.asset,offset=offset)
                    identity=(reply['sha'],reply['size'])
                    if expected is None:expected=identity
                    if identity!=expected or reply['offset']!=offset:raise Error('STATE_CONFLICT','File version changed; retry a pinned snapshot.')
                    raw=base64.b64decode(reply['data'],validate=True)
                    if not raw and offset<reply['size']:raise Error('SYNC_PROTOCOL','Empty transfer before completion.')
                    out.write(raw);digest.update(raw);offset+=len(raw)
                    if offset>=reply['size']:break
                out.flush();os.fsync(out.fileno())
            if offset!=expected[1] or digest.hexdigest()!=expected[0]:raise Error('CHECKSUM_MISMATCH','Downloaded content differs.')
            os.link(tmp,target)
        finally:Path(tmp).unlink(missing_ok=True)
        return {'thread':args.id,'path':str(target),'bytes':offset,'read_only_source':True,'ownership_changed':False}


@networked
def execute(args, root):
    if getattr(args,'offline',False) or getattr(args,'dry_run',False):
        raise Error('INPUT_INVALID','Online access needs current authorization; omit offline and dry-run.')
    wb=Workbench(root)
    with wb.base.locked():
        wb.load(); online=Online(wb); verb=args.thread_command
        if verb=='access':
            if not args.visibility:
                if args.to or args.files or args.share_projects:raise Error('INPUT_INVALID','Choose a visibility before changing readers or files.')
                result=online.status(args.id)
                if result is None:raise Error('NOT_FOUND','This Thread has no online access entry.')
            else:result=online.publish(args.id,args.visibility,args.to,args.files,args.expect_acl,args.share_projects)
        elif verb=='browse':
            if getattr(args,'changes',None):
                if any((args.asset,args.output,args.files,args.records,args.record,args.version)) or not args.id:raise Error('INPUT_INVALID','Use a selected Thread and --changes alone; pin --snapshot for subsequent pages.')
                return online.call('thread_access_changes',thread=args.id,since=args.changes,revision=args.snapshot,offset=args.offset,limit=args.limit),wb.base.head
            if args.output and not args.asset:raise Error('INPUT_INVALID','Choose a file with --asset before downloading.')
            if args.asset:result=online.download(args)
            elif args.id and args.records:
                result=online.call('thread_access_records',thread=args.id,revision=args.snapshot,record=args.record,offset=args.offset,limit=args.limit)
            elif args.id and args.files:
                result=online.call('thread_access_files',thread=args.id,revision=args.snapshot,offset=args.offset,limit=args.limit)
            elif args.id:
                result=online.call('thread_access_read',thread=args.id,revision=args.snapshot,record=args.record,version=args.version,offset=args.offset,chars=args.chars)
            else:
                result=online.call('thread_access_list',direct=args.direct,owner=args.owner,offset=args.offset,limit=args.limit)
                result['tracking_suggestions']=grouped(wb,online.info['hub_id'],result['threads'])
        else:
            if args.show and (args.person or args.create or args.use or args.dismiss):
                raise Error('INPUT_INVALID','Use --show alone to open a tracking Thread.')
            if args.show:
                tid=wb.find(args.show,'thread')['doc']['meta']['id']
                people=[pid for pid,ids in bindings(wb,online.info['hub_id']).items() if tid in ids]
                if len(people)!=1:raise Error('NOT_FOUND','Choose a private Thread tracking one person on this Hub.')
                rows=online.direct(people[0])
                result={'thread':tid,'sources':rows,'server_current':True,'source_count':len(rows),'notes_preserved':True}
                if online.info.get('messages_protocol'):
                    result['messages']=online.call('thread_message_list',folder='chat',limit=20,before=None,unread=False,peer=people[0],thread=None,kind=None)
            else:
                rows=online.direct(args.person); people=grouped(wb,online.info['hub_id'],rows)
                if args.person and not people and (args.create or args.use) and online.info.get('messages_protocol'):
                    incoming=online.call('thread_message_list',folder='inbox',limit=1,before=None,unread=False,peer=args.person,thread=None,kind=None)['messages']
                    if incoming:
                        m=incoming[0];people=[dict(owner_id=m['sender'],account=m['sender_account'],name=m['sender_name'] or m['sender_account'])]
                if args.create or args.use or args.dismiss:
                    if not args.person or len(people)!=1:raise Error('INPUT_INVALID','Select one person who has made work directly visible to you.')
                    if args.dismiss:
                        ignored=wb.client.setdefault('tracking_dismissed',{}).setdefault(online.info['hub_id'],[])
                        if people[0]['owner_id'] not in ignored:ignored.append(people[0]['owner_id']);wb.save_client()
                        result={'dismissed':True,'created':False,'owner_id':people[0]['owner_id']}
                    else:result=attach(wb,online.info,people[0],args.use)
                else:result={'people':people,'server_current':True,'automatic_creation':False}
        shared_rows = []
        if isinstance(result, dict):
            if result.get('thread') and result.get('content_revision'):
                shared_rows.append(result)
            for key in ('threads', 'sources'):
                shared_rows.extend(row for row in result.get(key, []) if isinstance(row, dict) and row.get('content_revision'))
        if shared_rows and not getattr(args, 'snapshot', None):
            from .thread_execution_reader import cache_shared
            cache_shared(wb, online.info['hub_id'], shared_rows)
        return result,wb.base.head


def refresh_published(wb, personal, tid, sha):
    """An explicit owner sync updates an existing grant; it never creates one."""
    config=personal.state['connection']
    try: current=rpc(config,{'method':'thread_access_status','thread':tid})
    except Error as exc:
        if exc.code in {'NOT_FOUND','INPUT_INVALID','FEATURE_UNAVAILABLE','COMMAND_DENIED'}:return None
        raise
    if current.get('actor')!=personal.state['actor'] or not current.get('writable'):
        raise Error('AUTH_DENIED','Only the owner can refresh published Thread content.')
    if current['visibility']=='private':return None
    if current.get('migration_review'):
        return {'accepted':False,'state':'review_required','thread':tid,'reason':'Review preserved private history/files, then explicitly publish with thread access; previous public snapshot retained.'}
    return rpc(config,{'method':'thread_access_set','thread':tid,'sha':sha,
                      'visibility':current['visibility'],'readers':current['readers'],'expect':current['acl_revision']})
