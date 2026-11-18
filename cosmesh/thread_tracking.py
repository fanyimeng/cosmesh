"""A person's incoming work is followed through an ordinary private Thread."""
import json

from .errors import Error
from .personal_protocol import packed

MARKER = '<!-- cosmesh-tracking-v1 '


def registration(doc):
    body=doc['body']
    if MARKER not in body:return None
    try: value=json.loads(body.split(MARKER,1)[1].split(' -->',1)[0])
    except ValueError:return None
    return value if isinstance(value,dict) else None


def bindings(wb, hub_id):
    found = {}
    for item in wb.catalog.values():
        meta = item['doc']['meta']
        if item['repo'] != 'local' or meta['kind'] != 'entry': continue
        value=registration(item['doc'])
        if not isinstance(value, dict) or value.get('hub_id') != hub_id or not isinstance(value.get('person_id'), str): continue
        parent = wb.find(meta['thread'], 'thread')
        if parent['repo'] != 'local' or parent['doc']['meta']['author'] != wb.author_for(parent): continue
        found.setdefault(value['person_id'], set()).add(meta['thread'])
    return found


def grouped(wb, hub_id, rows):
    known = bindings(wb, hub_id); people = {}
    for row in rows:
        if row['visibility'] != 'restricted' or row['writable']: continue
        person = people.setdefault(row['owner_id'], dict(owner_id=row['owner_id'], account=row['owner_account'],
                                  name=row['owner_name'], threads=[]))
        person['threads'].append(row)
    for person in people.values():
        current = sorted(known.get(person['owner_id'], []))
        candidates = [x['doc']['meta']['id'] for x in wb.catalog.values() if x['repo']=='local'
                      and x['doc']['meta']['kind']=='thread' and x['doc']['meta']['title'] in (person['name'],person['account'])]
        person.update(tracking_threads=current, reuse_candidates=candidates,
                      suggestion='open' if len(current)==1 else 'choose_existing' if current or candidates else 'create',
                      suggested_title=person['name'], automatic_creation=False)
        if not current and person['owner_id'] in wb.client.get('tracking_dismissed',{}).get(hub_id,[]):
            person.update(suggestion='none',dismissed=True)
    return list(people.values())


def attach(wb, info, person, use=None):
    known = sorted(bindings(wb, info['hub_id']).get(person['owner_id'], []))
    if known:
        if len(known) != 1: raise Error('AMBIGUOUS_ID', 'Several private Threads already track this person; reconcile them before creating another.')
        if use and wb.find(use,'thread')['doc']['meta']['id'] != known[0]:
            raise Error('STATE_CONFLICT', 'This person already has a tracking Thread; reuse it.')
        return {'thread': known[0], 'created': False, 'reused': True, 'owner_id':info['person']['id']}
    key = 'track:'+info['hub_id']+':'+person['owner_id']
    if use:
        item = wb.find(use,'thread')
        if item['repo']!='local' or item['doc']['meta']['author'] != wb.author_for(item):
            raise Error('AUTH_DENIED', 'Use a private Thread you own.')
        tid = item['doc']['meta']['id']
        if any(tid in ids and pid != person['owner_id'] for pid,ids in bindings(wb,info['hub_id']).items()):
            raise Error('STATE_CONFLICT', 'This Thread already tracks another person; choose a different private Thread.')
    else:
        tid = wb.create(person['name'], 'Follow work this person has explicitly made visible to me. My notes and actions stay here.', key=key)['id']
        wb.load()
    value = {'hub_id':info['hub_id'], 'person_id':person['owner_id'], 'account':person['account']}
    wb.append(tid, 'Follow '+person['name']+"'s work shared directly with me. Read sources online as needed.\n\n"+MARKER+packed(value)+' -->', title='Tracking connection', key=key+':binding')
    wb.load()
    return {'thread':tid,'created':not bool(use),'reused':bool(use),'owner_id':info['person']['id']}


def view(wb, tid, offline=False):
    item=wb.find(tid,'thread')
    if item['repo']!='local':return None
    rows=[registration(r['doc']) for r in wb.records(item['doc']['meta']['id'])]
    rows=[r for r in rows if r and isinstance(r.get('person_id'),str) and isinstance(r.get('hub_id'),str)]
    if not rows:return None
    if offline:return {'server_current':False,'state':'offline','sources':None}
    try:
        from .thread_access_cli import Online
        online=Online(wb)
        targets={r['person_id'] for r in rows if r['hub_id']==online.info['hub_id']}
        if len(targets)!=1:raise Error('STATE_CONFLICT','Choose the matching Hub and one tracked person.')
        sources=online.direct(next(iter(targets)))
        result={'server_current':True,'sources':sources,'source_count':len(sources)}
        if online.info.get('messages_protocol'):
            result['messages']=online.call('thread_message_list',folder='chat',limit=20,before=None,unread=False,peer=next(iter(targets)),thread=None,kind=None)
        return result
    except Error as exc:
        return {'server_current':False,'state':'unavailable','sources':None,'error':exc.as_dict()}


def receive(wb, online, messages):
    """File current incoming references into one owned private Thread per stable person."""
    from .thread_messages_cli import personal
    result=[];known=bindings(wb,online.info['hub_id']);self_id=online.info['person']['id']
    for message in messages:
        peer=message['sender']
        if peer==self_id or self_id not in message['recipients'] or message['historical']:continue
        try:
            local=sorted(known.get(peer,[]))
            if len(local)>1:raise Error('AMBIGUOUS_ID','Choose the existing person Thread before automatic filing.')
            current=online.call('thread_message_tracker',message=message['id'],preferred=None,title=None,create=False)
            tid=current['thread']
            if tid and local and tid!=local[0]:raise Error('STATE_CONFLICT','The Hub and this device track this person in different Threads; retain both and reconcile explicitly.')
            if not tid:
                name=message['sender_name'] or message['sender_account'];title=name
                matches=[r for r in wb.catalog.values() if r['repo']=='local' and r['doc']['meta']['kind']=='thread' and r['doc']['meta']['title']==title]
                if matches and not local:
                    # A matching name alone never proves the identity of an existing Thread.
                    result.append({'person':peer,'state':'choose_existing','candidates':[r['doc']['meta']['id'] for r in matches]});continue
                current=online.call('thread_message_tracker',message=message['id'],preferred=local[0] if local else None,title=title,create=True)
                tid=current['thread']
            if tid not in wb.catalog:
                personal(wb,online).restore(tid)
            item=wb.find(tid,'thread')
            access=online.status(tid)
            if item['repo']!='local' or item['doc']['meta']['author']!=wb.author_for(item) or access and access['visibility']!='private':
                raise Error('AUTH_DENIED','Incoming references require your own private tracking Thread.')
            # Only a pointer is filed; listing does not mark read or download the source Thread.
            wb.append(tid,'消息 '+message['id']+'\n来源 Thread '+message['thread']+'\n固定记录 '+message['source']['record']+'@'+message['source']['version'],
                      title='收到消息',key='received:'+message['id'])
            wb.load();known[peer]={tid}
            message['tracking_thread']=tid
            result.append({'person':peer,'thread':tid,'state':'filed','created':current.get('created',False)})
        except Error as exc:
            result.append({'person':peer,'state':'pending','error':exc.as_dict()})
    return result
