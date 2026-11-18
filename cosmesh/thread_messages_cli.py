"""Explicit delivery of selected records, and a current, identity-bound mailbox."""
from .errors import Error
from .connections import networked
from .personal import initialize
from .personal_protocol import digest,packed,private_dir,read_json,save_json
from .thread_access_cli import Online
from .thread_personal import ThreadPersonal
from .thread_protocol import content_version
from .threads import Workbench
from .thread_messages import FORMAT


def personal(wb,online):
    initialize(wb);p=ThreadPersonal(wb)
    if p.state.get('connection') not in (None,online.config) or p.state.get('actor') not in (None,online.info['person']['account']):
        raise Error('AUTH_DENIED','Personal content and messages must use the same Hub identity.')
    p.state.update(connection=online.config,actor=online.info['person']['account']);p.save()
    return p


def send(wb,args,online):
    from .cli import read_input
    from .protocol import MAX_DOCUMENT
    verb=args.thread_command
    reply=getattr(args,'reply_to',None)
    tid=args.id
    targets=getattr(args,'to',None) or []
    if isinstance(targets,str):targets=[targets]
    if verb=='reply':
        reply=args.id;tid=args.thread
        if not tid:raise Error('INPUT_INVALID','Choose your own reply Thread with --thread ID; the source Thread belongs to its owner.')
        if not online:raise Error('INPUT_INVALID','For an offline reply use thread send OWN_THREAD --to USER --reply-to MESSAGE --local-only.')
        parent=online.call('thread_message_read',id=reply,offset=0,chars=1)
        targets=targets or [parent['sender']]
    if not tid or not targets:raise Error('INPUT_INVALID','Choose your own Thread and explicit --to recipient.')
    item=wb.find(tid,'thread');tid=item['doc']['meta']['id']
    if item['repo']!='local' or item['doc']['meta']['author']!=wb.author_for(item):raise Error('AUTH_DENIED','Send from a Thread you own.')
    if getattr(args,'ref',None):raise Error('INPUT_INVALID','Include a pinned online source in the message body, or use --record to select an existing record.')
    if getattr(args,'generation_file',None):
        import json
        wb.generation=json.loads(read_input(args.generation_file,MAX_DOCUMENT))
        if not isinstance(wb.generation,dict) or set(wb.generation)-{'tool','model','instruction','verification'} or any(not isinstance(v,str) or len(v)>8000 for v in wb.generation.values()):
            raise Error('INPUT_INVALID','Use bounded generation metadata.')
        wb.generation['verification']='client_reported'
    text=getattr(args,'body','')
    if hasattr(args,'file'):text=read_input(args.file,MAX_DOCUMENT)
    if len(text.encode())>MAX_DOCUMENT:raise Error('INPUT_TOO_LARGE','Message exceeds the document limit.')
    record=getattr(args,'record',None)
    if record and text:raise Error('INPUT_INVALID','Choose an existing --record or new message text.')
    if not record and not text.strip():raise Error('INPUT_INVALID','Provide message text or --record ID.')
    if getattr(args,'interaction_summary',None) or getattr(args,'interaction_summary_file',None):
        from .thread_chat import with_summary
        summary=getattr(args,'interaction_summary',None)
        if getattr(args,'interaction_summary_file',None):summary=read_input(args.interaction_summary_file,MAX_DOCUMENT)
        text=with_summary(text,summary)
    kind='reply' if reply else 'request' if verb=='request' else getattr(args,'kind','message')
    intent=dict(thread=tid,recipients=sorted(set(targets)),reply=reply,body=text,record=record,title=getattr(args,'title',None),kind=kind)
    fingerprint=digest(packed(intent).encode());key=getattr(args,'idempotency_key',None) or 'message:'+fingerprint
    # Keep delivery state outside published Git; retries freeze exactly one record and payload.
    initialize(wb)
    directory=private_dir(wb.base.path('.cosmesh/personal-v1/message-outbox'))
    path=directory/(digest(key.encode())+'.json');saved=read_json(path)
    if saved and saved['fingerprint']!=fingerprint:raise Error('IDEMPOTENCY_CONFLICT','This send key belongs to different input.')
    if online and saved and saved.get('hub_id') and (saved['hub_id']!=online.info['hub_id'] or saved.get('person_id')!=online.info['person']['id']):
        raise Error('AUTH_DENIED','Retry this message with its original Hub identity.')
    if saved and saved.get('receipt'):
        return dict(saved['receipt'],saved_locally=True,replayed=True)
    if online:
        if saved and saved.get('payload'):
            receipt=online.call('thread_message_send',**saved['payload'])
            if not receipt.get('accepted') or not receipt.get('sent'):raise Error('SYNC_PROTOCOL','The Hub did not confirm delivery.')
            receipt.update(saved_locally=True,delivery_state='accepted',record=saved['record'])
            saved['receipt']=receipt;save_json(path,saved);return receipt
        online.call('thread_message_preflight',thread=tid,recipients=targets,reply=reply)
    if not saved:
        if record:
            source=wb.find(record)
            if source['repo']!='local' or source['doc']['meta'].get('thread')!=tid:raise Error('INPUT_INVALID','Choose an ordinary record in your selected Thread.')
        else:
            added=wb.append(tid,text,record_type='result' if kind=='result' else 'note',title=intent['title'],key='message-record:'+digest(key.encode()))
            wb.load();source=wb.find(added['id'])
        saved={'fingerprint':fingerprint,'record':source['doc']['meta']['id'],'version':content_version(source['doc']), 'intent':intent}
        save_json(path,saved)
    if not online:return {'sent':False,'saved_locally':True,'delivery_state':'draft','record':saved['record'],'key':key}
    if 'payload' not in saved:
        sha=personal(wb,online).synchronize(tid)['accepted_snapshot']
        saved.update(hub_id=online.info['hub_id'],person_id=online.info['person']['id'],payload=dict(thread=tid,recipients=targets,reply=reply,snapshot=sha,
                     record=saved['record'],version=saved['version'],kind=kind,key=key))
        save_json(path,saved)
    receipt=online.call('thread_message_send',**saved['payload'])
    if not receipt.get('accepted') or not receipt.get('sent'):raise Error('SYNC_PROTOCOL','The Hub did not confirm delivery; the pending message is retained.')
    receipt.update(saved_locally=True,delivery_state='accepted',record=saved['record'])
    saved['receipt']=receipt;save_json(path,saved)
    return receipt


def receive(wb,online,result):
    from .thread_tracking import receive as track_received
    result['tracking']=track_received(wb,online,result.get('messages',[]))
    return result


@networked
def execute(args,root):
    if getattr(args,'dry_run',False):raise Error('INPUT_INVALID','Use --local-only to prepare a draft without sending.')
    wb=Workbench(root)
    with wb.base.locked():
        wb.load();verb=args.thread_command
        draft=getattr(args,'local_only',False) or getattr(args,'offline',False)
        if draft and verb not in {'send','request','reply'}:raise Error('OFFLINE','Current message permissions require the Hub. Local Thread notes remain available offline.')
        online=None if draft else Online(wb)
        if online and online.info.get('messages_protocol')!=FORMAT:raise Error('FEATURE_UNAVAILABLE','Upgrade the Hub for owner-based Thread messages; no legacy public delivery fallback is used.')
        if verb in {'send','request','reply'}:result=send(wb,args,online)
        elif verb=='search':
            if args.contact and not args.messages:raise Error('INPUT_INVALID','Use --messages with --with for a person conversation.')
            payload=dict(query=args.query,thread=args.thread,offset=args.offset,limit=args.limit)
            if args.messages:payload['peer']=args.contact
            result=online.call('thread_message_search' if args.messages else 'thread_access_search',**payload)
        elif verb=='mail':
            if args.mail_action=='mark':result=online.call('thread_message_mark',ids=args.ids,seen=args.seen)
            else:result=online.call('thread_message_read',id=args.id,offset=args.offset,chars=args.chars)
        else:
            if verb=='chat' and (args.date or args.include_progress or args.offset or args.tui):
                raise Error('COMMAND_RETIRED','Use --with and --before for current messages; read historical work with thread browse. Chat now uses plain text or JSON.')
            folder='chat' if verb=='chat' else 'outbox' if verb=='outbox' or getattr(args,'sent',False) else 'inbox'
            tid=getattr(args,'thread',None) or (getattr(args,'id',None) if verb=='chat' else None)
            peer=args.contact
            if verb=='chat' and tid:
                from .thread_tracking import bindings
                found=[pid for pid,ids in bindings(wb,online.info['hub_id']).items() if tid in ids]
                if len(found)==1:peer=found[0];tid=None
            result=online.call('thread_message_list',folder=folder,limit=args.limit,before=args.before,
                         unread=getattr(args,'unread',False),peer=peer,thread=tid,
                         kind='request' if getattr(args,'view',None)=='requests' else None)
            receive(wb,online,result)
            result['document']='\n'.join((m['sender_name'] or m['sender'])+' · '+m['title']+' · '+('未读' if m['unread'] else '已收录')+'\n'+m['excerpt']+'\n'+m['id'] for m in result['messages']) or '暂无符合条件的消息。'
        args._thread_sync_state={'state':'local_only' if draft else 'synced','identity_verified':not draft}
        return result,wb.base.head
