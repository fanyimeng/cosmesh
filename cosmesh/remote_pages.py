"""One prepared-document RPC, then private local reads; no Git fetch or files."""
import json

from .errors import Error
from .store import atomic
from .threads import now


def contact(wb,args):
    from .status_pages import cache_root,digest
    from .spaces import rpc
    connection=wb.client.get('connection')
    if not connection:return None,False
    actor=wb.client.get('actor')
    if not actor:return None,False
    config=json.loads(wb.base.path('.cosmesh/config.json').read_text())
    payload={'method':'thread_chat_ready','peer':args.contact,'rounds':args.rounds,'before':args.before,
             'date':args.date,'include_progress':args.include_progress,'timezone':config['timezone']}
    root=cache_root(wb);path=root/('remote-contact-'+digest([actor,connection,payload])+'.json')
    stamp=wb.path.stat().st_mtime_ns
    cached=json.loads(path.read_text()) if path.is_file() else None
    if cached and not getattr(args,'require_fresh',False) and cached['source_stamp']==stamp:
        return cached['data'],False
    if getattr(args,'offline',False):return None,False
    if not getattr(args,'require_fresh',False) and not args.before:
        from .status_pages import prepared_read
        local=prepared_read(wb,args)
        if local is not None:return local[0],False
    try:reply=rpc(connection,payload)
    except Error as error:
        if error.code in {'FEATURE_UNAVAILABLE','INPUT_INVALID','COMMAND_DENIED'}:return None,False
        if cached:
            value=cached['data'];value['freshness'].update(updating=True,server_current=False,refresh_error=error.code)
            return value,False
        raise
    if reply.get('actor')!=actor:raise Error('AUTH_DENIED','Prepared view identity differs from this Mesh connection.')
    if not reply.get('ready'):
        if cached:
            value=cached['data'];value['freshness'].update(updating=True,server_current=False)
            return value,False
        return None,True
    value=reply['data']
    if value.get('format')!='contact.chat.v1' or any(ref['repo']=='local' for ref in value['source_index']):
        raise Error('SYNC_PROTOCOL','Unexpected prepared conversation document.')
    if any(m['author'] not in {actor,value['peer']['account']} for m in value['messages']):
        raise Error('SYNC_PROTOCOL','Prepared conversation contains another participant.')
    value['fetched_at']=now()
    atomic(path,json.dumps({'source_stamp':stamp,'data':value},ensure_ascii=False))
    return value,False
