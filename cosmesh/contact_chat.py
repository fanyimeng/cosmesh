"""A frozen, disposable contact view over shared records, not an IM store."""
import base64
import hashlib
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
import re

from .errors import Error


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def decode_cursor(value):
    try:
        if not isinstance(value,str) or len(value)>1000:raise ValueError()
        token=json.loads(base64.urlsafe_b64decode(value.encode()+b'='*(-len(value)%4)))
        if set(token)!={'view','offset'} or type(token['offset'])is not int or token['offset']<0:raise ValueError()
        if not isinstance(token['view'],str) or not re.fullmatch(r'mesh/contact-[a-f0-9]{64}',token['view']):raise ValueError()
        return token
    except (ValueError,TypeError,KeyError,UnicodeError):raise Error('INPUT_INVALID','Use the previous next_cursor without modifying it.')


def read(con,args,identity,users,freshness):
    from .knowledge import select_user
    from .status_receipts import short_title
    me=identity['actor'];peer=select_user(users,args.contact)
    if peer['account']==me:raise Error('INPUT_INVALID','Choose another user for a contact conversation.')
    if not 1<=args.rounds<=20:raise Error('INPUT_INVALID','Use 1 to 20 conversation rounds.')
    if args.date:
        try:date.fromisoformat(args.date)
        except ValueError:raise Error('INPUT_INVALID','Use a local calendar date YYYY-MM-DD.')
    offset=0;view=None
    alias='mesh/contact-latest-'+hashlib.sha256(encoded([me,peer['account'],args.date,args.include_progress]).encode()).hexdigest()
    if args.before:
        try:
            token=decode_cursor(args.before)
            row=con.execute('SELECT body FROM pages WHERE key=?',(token['view'],)).fetchone()
            if not row:raise Error('VIEW_EXPIRED','This disposable view expired; reopen the latest conversation.')
            view=json.loads(row[0]);offset=token['offset'];key=token['view']
            if view['actor']!=me or view['peer']['account']!=peer['account'] or view['date']!=args.date or view['include_progress']!=args.include_progress:
                raise Error('INPUT_INVALID','Continue the same contact/date/progress view with this cursor.')
        except (ValueError,TypeError,KeyError,UnicodeError):raise Error('INPUT_INVALID','Use the previous next_cursor without modifying it.')
    if not args.before and freshness.get('source_token'):
        pointer=con.execute('SELECT body FROM pages WHERE key=?',(alias,)).fetchone()
        if pointer:
            pointer=json.loads(pointer[0])
            if pointer.get('source_token')==freshness['source_token']:
                row=con.execute('SELECT body FROM pages WHERE key=?',(pointer['view'],)).fetchone()
                if row:view=json.loads(row[0]);key=pointer['view']
    if view is None:
        rows=con.execute("SELECT e.data,t.card FROM entries e JOIN entry_meta m ON m.id=e.id JOIN threads t ON t.id=e.thread "
                         "WHERE t.repo!='local' AND m.author IN (?,?) AND e.kind!='naming' ORDER BY e.created,e.id",(me,peer['account'])).fetchall()
        entries=[(json.loads(row['data']),json.loads(row['card'])) for row in rows]
        direct={r['id'] for r,_ in entries if r['kind']=='request' and {r['author'],r['to']}=={me,peer['account']}}
        request_states={r['id']:r['request_status'] for r,_ in entries if r['id'] in direct}
        by_id={r['id']:r for r,_ in entries}
        selected=[]
        zone=ZoneInfo(identity['timezone'])
        for entry,card in entries:
            parent=next((ref['source']['id'] for ref in entry['refs'] if ref['relation']=='request'),None)
            old=next((ref['source']['id'] for ref in entry['refs'] if ref['relation']=='supersedes'),None)
            seen=set()
            while not parent and old in by_id and old not in seen:
                seen.add(old);original=by_id[old]
                parent=original['id'] if original['id'] in direct else next((ref['source']['id'] for ref in original['refs'] if ref['relation']=='request'),None)
                old=next((ref['source']['id'] for ref in original['refs'] if ref['relation']=='supersedes'),None)
            topic=entry['id'] if entry['id'] in direct else parent
            progress=False
            if topic not in direct:
                if not args.include_progress or entry['kind'] not in {'note','result','summary','correction'}:continue
                topic=card['id'];progress=True
            if args.date and datetime.fromisoformat(entry['created_at']).astimezone(zone).date().isoformat()!=args.date:continue
            selected.append({**entry,'topic':topic,'thread':card['id'],'thread_name':short_title(card),'shared_progress':progress,
                             'request_state':request_states.get(topic)})
        # Preserve global time order when topics interleave.
        turns=[]
        for entry in selected:
            if turns and turns[-1][0]['topic']==entry['topic'] and turns[-1][0]['author']==entry['author']:turns[-1].append(entry)
            else:turns.append([entry])
        rounds=[]
        for turn in turns:
            if rounds and len(rounds[-1]['turns'])==1 and rounds[-1]['turns'][0][0]['topic']==turn[0]['topic']:
                rounds[-1]['turns'].append(turn);rounds[-1]['open']=False
            else:rounds.append({'turns':[turn],'open':True})
        view={'format':1,'actor':me,'peer':peer,'date':args.date,'include_progress':args.include_progress,
              'rounds':rounds,'freshness':freshness,'timezone':identity['timezone']}
        # Reuse the existing derived pages table. Content-stable keys keep
        # repeated refreshes of an unchanged conversation from making copies.
        version=hashlib.sha256(encoded([me,peer,args.date,args.include_progress,rounds]).encode()).hexdigest()
        key='mesh/contact-'+version
        con.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?)',(key,version,encoded(view)))
        con.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?)',(alias,version,encoded({'view':key,'source_token':freshness.get('source_token')})))
        con.commit()
    return packet(view,key,offset,args.rounds)


def packet(view,key,offset,count):
    me=view['actor'];peer=view['peer']
    end=max(0,len(view['rounds'])-offset);start=max(0,end-count)
    rounds=view['rounds'][start:end];sources=[];positions={}
    def add(ref):
        key=tuple(ref[k] for k in ('repo','id','version'))
        if key not in positions:positions[key]=len(sources);sources.append(ref)
        return positions[key]
    messages=[];display_rounds=[]
    for number,round_ in enumerate(rounds,start+1):
        indexes=[]
        for turn in round_['turns']:
            # All original records remain separately locatable after merging.
            text='';parts=[]
            for entry in turn:
                if text:text+='\n'
                begin=len(text);text+=entry['interaction_summary']['text']
                parts.append({'id':entry['id'],'source':add(entry['source']),'span':[begin,len(text)],
                              'origin':entry['interaction_summary']['origin'],'truncated':entry['interaction_summary']['truncated'],
                              'superseded_by':[add(ref) for ref in entry['superseded_by']]})
            last=turn[-1]
            indexes.append(len(messages))
            messages.append({'id':last['id'],'kind':last['kind'],'author':last['author'],'author_name':last['author_name'],
                             'created_at':last['created_at'],'local_time':datetime.fromisoformat(last['created_at']).astimezone(ZoneInfo(view['timezone'])).strftime('%Y-%m-%d %H:%M'),
                             'thread':last['thread'],'thread_name':last['thread_name'],'round':number,
                             'side':'right' if last['author']==me else 'left','shared_progress':last['shared_progress'],
                             'interaction_summary':{'text':text,'origin':'author' if all(p['origin']=='author' for p in parts) else 'legacy_excerpt',
                                                    'truncated':any(p['truncated'] for p in parts)},
                             'parts':parts,'accepted':all(r['accepted'] for r in turn),
                             'request':{'id':last['topic'],**{k:last['request_state'][k] for k in ('state','confirmed','response_count')}} if last['request_state'] else None,
                             'superseded_by':list(dict.fromkeys(s for p in parts for s in p['superseded_by']))})
        display_rounds.append({'number':number,'open':round_['open'],'messages':indexes})
    next_offset=offset+len(rounds)
    cursor=base64.urlsafe_b64encode(encoded({'view':key,'offset':next_offset}).encode()).decode().rstrip('=') if start else None
    return {'format':'contact.chat.v1','title':'与'+(peer['name'] or peer['account'])+'的交流',
            'overall':'按时间排列；同话题同一人的连续补充合并，随后对方接话构成一轮。话题切换另起一轮，单方轮次保留；请求状态单独显示。',
            'peer':{'account':peer['account'],'name':peer['name'],'id':peer['id']},'messages':messages,'rounds':display_rounds,
            'total_rounds':len(view['rounds']),'source_index':sources,'freshness':view['freshness'],'private':False,
            'next_cursor':cursor,'view':key,'date':view['date'],'truncated':cursor is not None,
            'read_hint':'默认最近五轮。🔵对方在左，🔴自己在右，直接展示已存摘要；旧记录摘录标明来源。继续使用 next_cursor，只追加更早部分，避免重复。parts 保留每条原记录与版本；开放交流不等于未交付，正式状态按请求读取。'}
