"""A person's incoming work is followed through an ordinary private Thread."""
import json

from cosmesh.errors import Error
from cosmesh.personal_protocol import packed

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
