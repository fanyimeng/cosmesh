"""Authored interaction summaries in ordinary records; cached conversation views."""
import re
import unicodedata

from .errors import Error

HEADING = '## 交互摘要\n'


def summary(body, chars=1600):
    match = re.match(r'\A## 交互摘要[ \t]*\n(.*?)(?:\n## 正文[ \t]*\n|\Z)', body, re.S)
    text = match.group(1).strip() if match else body.strip()
    return {'text': text[:chars], 'truncated': len(text) > chars,
            'origin': 'author' if match else 'legacy_excerpt'}


def with_summary(body, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or re.search(r'(?m)^#{1,6}\s', text):
        raise Error('INPUT_INVALID', 'Provide a concise plain-text interaction summary (at most 4000 characters).')
    if body.startswith(HEADING):
        raise Error('INPUT_INVALID', 'Provide the interaction summary once, through the flag or existing body.')
    return HEADING + text.strip() + ('\n\n## 正文\n' + body if body.strip() else '\n')


def receipt(card, entries, total, offset, identity, freshness, requests, materials):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .status_receipts import overview_text, short_title
    sources = [card['source']]; positions = {tuple(card['source'][k] for k in ('repo','id','version')): 0}
    def add(ref):
        key = tuple(ref[k] for k in ('repo','id','version'))
        if key not in positions: positions[key] = len(sources); sources.append(ref)
        return positions[key]
    messages = []
    for entry in entries:
        own = entry['author'] in {identity['actor'], identity['local']}
        message = {k: entry[k] for k in ('id','kind','author','author_name','created_at','interaction_summary','accepted')}
        message.update(side='right' if own else 'left', source=add(entry['source']),
                       local_time=datetime.fromisoformat(entry['created_at']).astimezone(ZoneInfo(identity['timezone'])).strftime('%Y-%m-%d %H:%M'),
                       thread_name=short_title(card),
                       reply_to=next((r['source']['id'] for r in entry['refs'] if r['relation']=='request'), None),
                       superseded_by=[add(ref) for ref in entry['superseded_by']])
        messages.append(message)
    resources = dict(materials)
    resources['files'] = [{**f, **({'source':add(f['source'])} if 'source' in f else {})} for f in materials['files']]
    if resources['index']:resources['index']=add(resources['index'])
    return {'format':'thread.chat.v1',
            'title':short_title(card),'overall':overview_text(card),'thread':0,'source_index':sources,
            'messages':messages,'total':total,'offset':offset,'truncated':total>offset+len(entries),
            'freshness':freshness,'private':not card['shared'],
            'requests': [{k:r[k] for k in ('id','to','state','confirmed','response_count')} for r in requests],
            'materials':resources,
            'read_hint':'按现成交互摘要直接展示或简述；作者摘要随源记录保存在 Hub。旧记录只作摘录标记。需要正文／打开／核验时才展开，不重写已存摘要或自动回填旧记录。'}


def document(data):
    lines = ['# '+data['title'],'',data['overall'],'']
    if data.get('freshness',{}).get('updating'):lines+=['〔成品正在更新，当前显示上次内容〕','']
    lines += [line for _,line in screen_lines(data,79)]
    if not data['messages']:lines+=['暂无交互记录。']
    if data.get('next_cursor'):lines+=['继续位置：'+data['next_cursor']]
    elif data['truncated']:lines+=['〔可增加 offset 查看更早交互〕']
    return '\n'.join(lines)+'\n'


def width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def wrap(text, columns):
    lines = []; current = ''; count = 0
    # Terminal control bytes in authored text must never become terminal commands.
    text = ''.join(c for c in text if c in '\n\t' or unicodedata.category(c)[0] != 'C').replace('\t','  ')
    for c in text:
        size = width(c)
        if c == '\n' or (current and count+size > columns):
            lines.append(current); current = ''; count = 0
            if c == '\n': continue
        current += c; count += size
    if current or not lines: lines.append(current)
    return lines


def screen_lines(data, columns):
    """Plain cell-aligned lines; each line carries its selected message index."""
    result = []; bubble = max(8, min(76, columns-12, int(columns*.72)))
    for index, message in enumerate(data['messages']):
        authored = message['interaction_summary']['origin'] == 'author'
        labels={'request':'请求','reply':'回应','result':'成果','delivery':'交付','assign':'承接','note':'记录',
                'summary':'归纳','correction':'更正','adopt':'采用','cancel':'取消','decline':'拒绝','state':'状态','lifecycle':'收纳'}
        title = ('🔴 ' if message['side']=='right' else '🔵 ')+message['author_name']+' · '+labels.get(message['kind'],message['kind'])
        if message.get('local_time'):title+=' '+message['local_time']
        if message.get('thread_name'):title+=' · '+message['thread_name']
        if message.get('shared_progress'):title+='〔共享进展〕'
        title += '' if authored else '〔旧摘录〕'
        if message['superseded_by']: title += '〔有更正〕'
        if not message['accepted'] and not data['private']: title += '〔未确认发布〕'
        if message.get('request'):
            states={'pending':'待处理','accepted':'已承接','partial':'部分交付','delivered':'已交付','adopted':'已采用','declined':'已拒绝','cancelled':'已取消'}
            r=message['request'];title+='〔'+states[r['state']]+('／已有回应' if r['response_count'] else '')+'〕'
        body = message['interaction_summary']['text'] or '〔空记录〕'
        if message['interaction_summary']['truncated']: body += '〔截断〕'
        chunks = wrap(title,bubble)+wrap(body,bubble)
        size = max(width(line) for line in chunks)
        indent = max(0,columns-size-3) if message['side']=='right' else 2
        result += [(index,' '*indent+line) for line in chunks]+[(index,'')]
    return result


def tui(data, refresh, detail, older=None):
    import curses
    def run(window):
        nonlocal data
        try: curses.curs_set(0)
        except curses.error: pass
        selected = max(0,len(data['messages'])-1); top = None; status = ''
        while True:
            rows, columns = window.getmaxyx()
            if rows < 8 or columns < 25:
                window.erase()
                try: window.addstr(0,0,'请放大终端，q 退出。')
                except curses.error: pass
                window.refresh()
                if window.getch() in (ord('q'),27): break
                continue
            lines = screen_lines(data,columns-1); height = rows-5
            if top is None: top = max(0,len(lines)-height)
            top = min(max(0,top),max(0,len(lines)-height))
            window.erase()
            def put(y,text,attr=0):
                try: window.addstr(y,0,wrap(text,columns-1)[0],attr)
                except curses.error: pass
            put(0,data['title'],curses.A_BOLD)
            put(1,data['overall'])
            if data.get('freshness',{}).get('updating'):put(2,'成品正在更新，当前显示上次内容。')
            for y,(index,line) in enumerate(lines[top:top+height],3):
                put(y,line,curses.A_BOLD if index==selected else 0)
            put(rows-2,status or '↑↓滚动  j/k选择  Enter正文  p更早  r刷新  q退出')
            window.refresh(); key=window.getch(); status=''
            if key in (ord('q'),27): break
            if key==curses.KEY_UP: top-=1
            elif key==curses.KEY_DOWN: top+=1
            elif key==curses.KEY_PPAGE: top-=height
            elif key==curses.KEY_NPAGE: top+=height
            elif key in (ord('j'),ord('k')):
                selected=min(max(0,selected+(1 if key==ord('j') else -1)),max(0,len(data['messages'])-1))
                top=next((i for i,(index,_) in enumerate(lines) if index==selected),0)
            elif key==ord('r'):
                try: data=refresh();selected=max(0,len(data['messages'])-1);top=None
                except (Error,OSError) as error: status=str(error)
            elif key==ord('p') and older:
                try:
                    if not data.get('next_cursor'):status='已到当前视图开头。'
                    else:data=older(data['next_cursor']);selected=0;top=0
                except (Error,OSError) as error:status=str(error)
            elif key in (10,13,curses.KEY_ENTER) and data['messages']:
                try:
                    text=detail(data['messages'][selected],data['source_index']); expanded=wrap(text,columns-1); start=0
                    while True:
                        window.erase()
                        for y,line in enumerate(expanded[start:start+rows-2]): put(y,line)
                        put(rows-1,'正文：↑↓滚动，q/Enter返回');window.refresh();key=window.getch()
                        if key in (ord('q'),27,10,13): break
                        if key==curses.KEY_DOWN: start=min(start+1,max(0,len(expanded)-rows+2))
                        if key==curses.KEY_UP: start=max(0,start-1)
                except (Error,OSError) as error: status=str(error)
    curses.wrapper(run)
