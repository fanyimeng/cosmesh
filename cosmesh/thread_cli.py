"""Small agent-facing Thread commands, shared by cosmesh and cosbase."""
from pathlib import Path
import os

from .errors import Error
from .threads import Workbench, initialize, workspace_root


def add_commands(commands, command, body):
    group = command(commands, 'thread').add_subparsers(dest='thread_command', required=True)
    init = command(group, 'init', 'thread.init')
    init.add_argument('--user'); init.add_argument('--timezone', default='Asia/Shanghai')
    discover = command(group, 'discover', 'thread.discover')
    discover.add_argument('--ssh'); discover.add_argument('--service')
    p = command(group, 'sync', 'thread.sync'); p.add_argument('--pull', action='store_true')
    command(group, 'status', 'thread.status')
    command(group, 'maintenance', 'thread.maintenance')
    p = command(group, 'locate', 'thread.locate');p.add_argument('--workdir');p.add_argument('--session-id');p.add_argument('--host')
    p.add_argument('--agent');p.add_argument('--project-id');p.add_argument('--project-name')
    p = command(group,'bind-project','thread.bind-project');p.add_argument('id');p.add_argument('--agent',required=True)
    p.add_argument('--project-name');p.add_argument('--project-id');p.add_argument('--host');p.add_argument('--workdir')
    p.add_argument('--kind',choices=('project','workspace','directory'),default='project')
    p.add_argument('--host-file');p.add_argument('--remove',action='store_true')
    p = command(group, 'list', 'thread.list'); p.add_argument('--query'); p.add_argument('--mine', action='store_true');p.add_argument('--all',action='store_true')
    p.add_argument('--lifecycle', choices=('active', 'archived', 'trash', 'withdrawn', 'merged', 'all'))
    p = command(group, 'overview', 'thread.overview'); p.add_argument('id', nargs='?')
    p.add_argument('--mine', action='store_true'); p.add_argument('--query'); p.add_argument('--all', action='store_true')
    p.add_argument('--lifecycle', choices=('active', 'archived', 'trash', 'withdrawn', 'merged', 'all'))
    p.add_argument('--scope', choices=('all', 'base', 'mesh'), default='all')
    view = p.add_mutually_exclusive_group()
    view.add_argument('--details', action='store_true'); view.add_argument('--compact', action='store_true')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--host-file', help='Private, bounded metadata from supported host APIs; see guide show threads')
    p.add_argument('--continue', dest='continue_id', help='Read a selected Thread together with the compact private overview')
    p = command(group, 'page', 'thread.page'); p.add_argument('id', nargs='?')
    p.add_argument('--level', choices=('brief', 'status', 'index'), default='brief')
    p.add_argument('--scope', choices=('all', 'base', 'mesh'), default='all')
    p.add_argument('--user'); p.add_argument('--query'); p.add_argument('--all', action='store_true')
    p.add_argument('--limit', type=int, default=20); p.add_argument('--offset', type=int, default=0)
    p.add_argument('--refresh', action='store_true')
    presentation = p.add_mutually_exclusive_group()
    presentation.add_argument('--markdown', action='store_true')
    presentation.add_argument('--ready', action='store_true', help='Prepared document and a minimal fixed-source index')
    p.add_argument('--continue', dest='continue_id', help='Include the selected status page with a user overview')
    p = command(group, 'check', 'thread.check'); p.add_argument('id')
    p.add_argument('--scope', choices=('all', 'base', 'mesh'), default='all')
    p.add_argument('--limit', type=int, default=5)
    p = command(group, 'chat', 'thread.chat'); p.add_argument('id',nargs='?')
    p.add_argument('--with',dest='contact');p.add_argument('--rounds',type=int,default=5)
    p.add_argument('--before');p.add_argument('--date');p.add_argument('--include-progress',action='store_true')
    p.add_argument('--scope', choices=('all','base','mesh'), default='all')
    p.add_argument('--limit',type=int,default=30);p.add_argument('--offset',type=int,default=0)
    view = p.add_mutually_exclusive_group()
    view.add_argument('--tui',action='store_true');view.add_argument('--plain',action='store_true')
    p = command(group,'read','thread.read');p.add_argument('id');p.add_argument('--repo',required=True)
    p.add_argument('--offset',type=int,default=0);p.add_argument('--chars',type=int,default=8000)
    p = command(group, 'inbox', 'thread.inbox'); p.add_argument('--sent', action='store_true'); p.add_argument('--all', action='store_true')
    p.add_argument('--ready', action='store_true'); p.add_argument('--limit', type=int, default=10)
    for verb in ('show', 'context', 'continue', 'coordinate', 'backlinks'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id')
    p = command(group, 'create', 'thread.create'); p.add_argument('--title', required=True); body(p)
    p.add_argument('--ready', action='store_true')
    p.add_argument('--generation-file')
    p.add_argument('--english');p.add_argument('--chinese');p.add_argument('--intro')
    p = command(group,'name','thread.name');p.add_argument('id')
    p.add_argument('--english');p.add_argument('--chinese');p.add_argument('--intro')
    p.add_argument('--alias',action='append',default=[]);p.add_argument('--expect');p.add_argument('--sync',action='store_true')
    p = command(group, 'split', 'thread.split'); p.add_argument('id'); p.add_argument('--title', required=True); body(p)
    p.add_argument('--ref', action='append', default=[]);p.add_argument('--sync', action='store_true')
    p = command(group, 'merge', 'thread.merge'); p.add_argument('sources', nargs='+');p.add_argument('--into',required=True)
    body(p);p.add_argument('--sync',action='store_true')
    p = command(group, 'delete', 'thread.delete');p.add_argument('id');body(p);p.add_argument('--sync',action='store_true')
    for verb in ('note', 'result', 'summary', 'request', 'reply', 'assign', 'decline', 'deliver', 'adopt', 'cancel', 'correct', 'link', 'state', 'archive','restore','share', 'edit', 'bind'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id', **({'nargs':'?'} if verb == 'request' else {}))
        if verb != 'bind':
            p.add_argument('--sync', action='store_true')
            p.add_argument('--ready', action='store_true')
        if verb in ('note', 'result', 'summary', 'request', 'reply', 'assign', 'decline', 'deliver', 'adopt', 'cancel', 'correct', 'link', 'edit'):
            body(p)
        if verb == 'request':
            p.add_argument('--to', help='Recipient account, stable ID or real name; defaults to the Thread creator')
            p.add_argument('--maintenance', action='store_true', help='Use the Hub maintenance Thread')
        if verb in ('note', 'result', 'summary', 'request'):
            p.add_argument('--ref', action='append', default=[], help='Cite ID or pinned ID@VERSION; repeat for multiple sources')
        if verb in ('note','result','summary','edit','request','reply','assign','decline','deliver','adopt','cancel','correct','state','archive','restore'):
            p.add_argument('--generation-file')
        if verb in ('note','result','summary','request','reply','assign','decline','deliver','adopt','cancel','correct','state','archive','restore'):
            brief = p.add_mutually_exclusive_group()
            brief.add_argument('--interaction-summary');brief.add_argument('--interaction-summary-file')
        if verb == 'assign':
            p.add_argument('--work', action='append', required=True)
        if verb == 'deliver':
            p.add_argument('--result', action='append', required=True); p.add_argument('--partial', action='store_true')
        if verb == 'adopt':
            p.add_argument('--result', action='append', default=[])
        if verb == 'link':
            p.add_argument('--target', required=True)
        if verb == 'state':
            p.add_argument('--to', choices=('active', 'done', 'cancelled', 'archived'), required=True)
        if verb == 'edit':
            p.add_argument('--expect', required=True); p.add_argument('--title')
        if verb == 'bind':
            p.add_argument('--agent', required=True); p.add_argument('--workdir', required=True)
            p.add_argument('--session-id'); p.add_argument('--host')
        if verb=='share':
            p.add_argument('--attach',action='append',default=[])
            p.add_argument('--materials',help='Private format=1 selection of explicit files and Git code reference')
            p.add_argument('--resource',action='append',default=[],help='Reuse a fixed cosmesh://library/ reference')
            p.add_argument('--git');p.add_argument('--commit');p.add_argument('--code-path',action='append',default=[])
            p.add_argument('--summary-only',action='store_true');p.add_argument('--generation-file')


def execute(args):
    from .access import system_user
    from .cli import read_input
    from .protocol import MAX_DOCUMENT
    verb = args.thread_command
    root = workspace_root(getattr(args, 'root', None))
    dry, offline = getattr(args, 'dry_run', False), getattr(args, 'offline', False)
    fresh, publish = getattr(args, 'require_fresh', False), getattr(args, 'sync', False)
    if getattr(args, 'revision', None) or getattr(args, 'expect_revision', None):
        raise Error('INPUT_INVALID', 'Thread contexts carry separate source versions; use pinned references or edit --expect.')
    if offline and (fresh or publish or verb in {'discover', 'sync'}):
        raise Error('INPUT_INVALID', 'Offline Thread operations cannot contact the Hub.')
    if dry and (publish or fresh or verb in {'discover', 'sync'}):
        raise Error('INPUT_INVALID', 'Dry-run previews local changes and cannot synchronize.')
    if fresh and verb not in {'list', 'overview', 'page', 'check', 'chat', 'show', 'context', 'continue', 'coordinate', 'backlinks', 'inbox', 'status', 'maintenance'}:
        raise Error('INPUT_INVALID', '--require-fresh applies to reads and only pulls shared content.')
    if verb == 'overview' and ((args.scope == 'mesh' and args.host_file) or (args.scope == 'base' and fresh)):
        raise Error('INPUT_INVALID', 'Mesh overview excludes host metadata; Base overview stays local without --require-fresh.')
    if verb == 'overview' and args.continue_id and (not args.compact or args.scope == 'mesh'):
        raise Error('INPUT_INVALID', 'Use --compact and a private scope for combined continuation.')
    if verb == 'request' and bool(args.id) == args.maintenance:
        raise Error('INPUT_INVALID', 'Choose a Thread ID or --maintenance.')
    if verb == 'chat':
        if bool(args.id)==bool(args.contact):raise Error('INPUT_INVALID','Choose a Thread/request ID or --with REAL_NAME.')
        if args.contact:
            if args.scope=='base':raise Error('INPUT_INVALID','Contact conversations use shared Mesh records.')
            args.scope='mesh'
        elif args.before or args.date or args.include_progress:
            raise Error('INPUT_INVALID','Date, cursor and shared-progress filters apply to --with conversations.')
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if prefix and (root == Path(prefix).resolve() or Path(prefix).resolve() in root.parents):
        raise Error('PATH_INVALID', 'Keep the workbench outside the application installation.')
    if verb == 'init':
        return initialize(root, args.user or system_user()['user'], args.timezone, dry)
    if verb == 'discover' and not root.exists():
        initialize(root, system_user()['user'])
    wb = Workbench(root)
    if verb=='read':
        if dry:raise Error('INPUT_INVALID','Source reads do not use --dry-run.')
        from .thread_protocol import reference
        identifier,separator,version=args.id.partition('@')
        if not separator:raise Error('INPUT_INVALID','Use the fixed ID@VERSION from a conversation source index.')
        ref={'repo':args.repo,'id':identifier,'version':version};reference(ref)
        if not 1<=args.chars<=32000 or args.offset<0:raise Error('INPUT_INVALID','Use 1–32000 characters and a nonnegative offset.')
        args._thread_sync_state={'state':'local_only','identity_verified':False}
        if args.repo!='local' and not offline and wb.client.get('connection'):
            from .spaces import rpc
            result=rpc(wb.client['connection'],{'method':'thread_read','sources':[ref],'chars':args.chars,'offset':args.offset})
            if result.get('actor')!=wb.client.get('actor'):raise Error('AUTH_DENIED','Source read identity differs.')
            args._thread_sync_state={'state':'synced','identity_verified':True}
            return result['records'][0],None
        with wb.base.locked():
            wb.load();doc=wb.resolve(ref);end=args.offset+args.chars
            return {'source':ref,'author':doc['meta']['author'],'body':doc['body'][args.offset:end],
                    'truncated':end<len(doc['body']),'next_offset':end if end<len(doc['body']) else None},None
    if verb == 'check':
        args.level = 'status'; args.user = args.query = args.continue_id = None
        args.all = True; args.offset = 0
        args.refresh = args.markdown = args.ready = False
        args.check = True
    if verb == 'chat':
        args.level = 'index'; args.user = args.query = args.continue_id = None
        args.all = True; args.refresh = args.markdown = args.ready = False
        args.chat = True
    if verb in {'page','check','chat'}:
        args._thread_sync_state = {'state': 'local_only', 'identity_verified': False}
        if dry or (args.scope == 'base' and fresh) or (args.id and args.continue_id):
            raise Error('INPUT_INVALID', 'Status pages are local derived reads; omit --dry-run and keep Base offline.')
        if verb=='chat' and args.contact:
            from .remote_pages import contact
            prepared,preparing=contact(wb,args)
            if prepared is not None:
                if prepared.get('transport')=='prepared_hub_document':
                    args._thread_sync_state={'state':'cached' if prepared['freshness'].get('updating') else 'synced','identity_verified':True}
                return prepared,None
            if preparing:
                fresh=False;args.require_fresh=False;args._hub_page_preparing=True
        if fresh:
            with wb.base.locked():
                wb.load()
                if wb.synchronize(publish=False)['errors']:
                    raise Error('FRESHNESS_UNAVAILABLE', 'Some shared sources could not be refreshed; cached sources are retained.')
        from .status_pages import read_page
        result,revision=read_page(wb,args)
        if getattr(args,'_hub_page_preparing',False):result['freshness'].update(updating=True,server_current=False)
        return result,revision
    with wb.base.locked():
        wb.load()
        def finish(result, reload=False):
            if reload: wb.load()
            args._thread_sync_state = wb.status()
            if verb == 'overview' and args.compact:
                status = args._thread_sync_state
                repos = {ref['repo'] for ref in result['source_index']}
                args._thread_sync_state = {key: status[key] for key in ('state', 'identity_verified', 'last_sync')}
                # A continuation can cite repositories outside the limited overview rows.
                if 'continuation' in result: repos = {space['repo'] for space in status['spaces']}
                args._thread_sync_state['spaces'] = [
                    {key: space[key] for key in ('repo', 'local_revision', 'remote_revision', 'last_sync_at',
                                                'state', 'identity_verified', 'unpushed_count') if key in space}
                    for space in status['spaces'] if space['repo'] in repos]
            if verb == 'inbox' and args.ready:
                args._thread_sync_state = {key: args._thread_sync_state[key] for key in ('state','identity_verified','last_sync')}
            if not dry and not publish and verb not in {'status','sync','maintenance','list','overview','locate','inbox','show','context','continue','backlinks','coordinate'}:
                from .status_pages import schedule
                schedule(wb)
            if getattr(args,'ready',False) and verb not in {'overview','inbox'}:
                from .thread_checks import ready_write
                result = ready_write(result,verb)
                args._thread_sync_state = {key: args._thread_sync_state[key] for key in ('state','identity_verified','last_sync')}
            return result, wb.base.head
        if getattr(args,'generation_file',None):
            import json
            wb.generation=json.loads(read_input(args.generation_file,MAX_DOCUMENT))
            if not isinstance(wb.generation,dict) or set(wb.generation)-{'tool','model','instruction','verification'} or any(not isinstance(x,str) or len(x)>8000 for x in wb.generation.values()):
                raise Error('INPUT_INVALID','Generation JSON accepts bounded tool, model, instruction and verification text.')
            wb.generation['verification']='client_reported'
        if verb == 'discover':
            return finish(wb.discover(args.ssh, args.service))
        if verb == 'sync':
            return finish(wb.synchronize(publish=not args.pull))
        if fresh:
            update = wb.synchronize(publish=False)
            if update['errors']:
                raise Error('FRESHNESS_UNAVAILABLE', 'Some shared sources could not be refreshed; cached content is retained.')
        key = getattr(args, 'idempotency_key', None)
        body = read_input(args.file, MAX_DOCUMENT) if hasattr(args, 'file') else getattr(args, 'body', '')
        brief = getattr(args,'interaction_summary',None)
        if getattr(args,'interaction_summary_file',None): brief = read_input(args.interaction_summary_file,16000)
        if brief is not None:
            from .thread_chat import with_summary
            body = with_summary(body,brief)
        if verb == 'status':
            result = wb.status()
        elif verb == 'maintenance':
            result = wb.maintenance_entry()
        elif verb == 'locate':
            from .thread_lifecycle import locate
            if not any((args.workdir,args.session_id,args.project_id,args.project_name)):
                raise Error('INPUT_INVALID', 'Use the actual project, workspace, directory or Session ID.')
            result = locate(wb,args.workdir,args.session_id,args.host,args.agent,args.project_id,args.project_name)
        elif verb == 'overview':
            import json
            from .thread_overview import overview
            host = json.loads(read_input(args.host_file, MAX_DOCUMENT)) if args.host_file else None
            result = overview(wb, args.id, args.mine, args.query, args.lifecycle or ('all' if args.all else 'active'),
                              args.scope, args.details, args.limit, host, args.compact)
            if args.continue_id:
                from .thread_overview import with_continuation
                result = with_continuation(wb, result, args.continue_id, args.mine)
        elif verb == 'list':
            result = []
            for item in wb.catalog.values():
                doc = item['doc']
                if doc['meta']['kind'] != 'thread' or (args.mine and doc['meta']['author'] != (wb.config['user'] if item['repo'] == 'local' else wb.user)):
                    continue
                if args.query and args.query.casefold() not in (doc['meta']['title'] + '\n' + doc['body']+'\n'+'\n'.join(wb._thread_names[doc['meta']['id']]['aliases'])).casefold():
                    continue
                shown=wb.present(item)
                placement = args.lifecycle or ('all' if args.all else 'active')
                if placement != 'all' and shown['lifecycle'] != placement:continue
                result.append(shown)
            result.sort(key=lambda x:(x['created_at'], x['id']), reverse=True)
        elif verb == 'inbox':
            if not 1 <= args.limit <= 100:
                raise Error('INPUT_INVALID', 'Use a limit from 1 to 100.')
            result = []
            for item in wb.catalog.values():
                meta = item['doc']['meta']
                if meta.get('type') == 'request' and meta['author' if args.sent else 'to'] == wb.user:
                    state = wb.request_state(item)
                    if args.all or state['state'] not in {'declined', 'adopted', 'cancelled'}:
                        parent = wb.find(meta['thread'], 'thread')
                        result.append({'request': wb.present(item), 'thread_lifecycle': wb.present(parent)['lifecycle'], **state})
            result.sort(key=lambda x:x['request']['created_at'], reverse=True)
            if args.ready:
                from .thread_checks import ready_inbox, recipient_responses
                for row in result[:args.limit]:
                    row['response_count'] = len(recipient_responses(wb,wb.find(row['request']['id'])))
                result = ready_inbox(result,args.limit)
        elif verb == 'show':
            item = wb.find(args.id)
            result = wb.present(item) if item['doc']['meta']['kind'] == 'entry' else wb.context(item['doc']['meta']['id'])
        elif verb == 'context':
            result = wb.context(args.id)
        elif verb == 'continue':
            from .thread_lifecycle import continuation
            result = continuation(wb, args.id)
        elif verb == 'backlinks':
            result = wb.backlinks(args.id)
        elif verb == 'coordinate':
            result = wb.coordination(args.id)
        elif verb == 'create':
            naming = {'english':args.english,'chinese':args.chinese,'intro':args.intro,'aliases':[]} if any(x is not None for x in (args.english,args.chinese,args.intro)) else None
            result = wb.create(args.title, body, key, dry, naming)
        elif verb == 'name':
            from .thread_names import set_names
            result = set_names(wb,args.id,args.english,args.chinese,args.intro,args.alias,args.expect,key,dry)
        elif verb == 'share':
            from .thread_materials import publish as share_materials
            result = share_materials(wb,args,key,dry)
            if publish and 'publication' in result:return finish(result)
        elif verb == 'edit':
            result = wb.edit(args.id, args.expect, args.title, body if hasattr(args, 'body') or hasattr(args, 'file') else None, key, dry)
        elif verb == 'bind':
            result = wb.bind(args.id, args.agent, args.workdir, args.session_id, args.host, dry)
        elif verb == 'bind-project':
            import json
            from .agent_projects import bind
            observation = json.loads(read_input(args.host_file, MAX_DOCUMENT)) if args.host_file else None
            result = bind(wb,args.id,args.agent,args.project_name,args.project_id,args.kind,args.host,args.workdir,observation,args.remove,dry)
        elif verb in {'archive','restore','delete'}:
            from .thread_lifecycle import placement
            result=placement(wb,args.id,verb,body,key,dry)
        elif verb == 'split':
            from .thread_lifecycle import split
            result=split(wb,args.id,args.title,body,[wb.citation(x) for x in args.ref],key,dry)
        elif verb == 'merge':
            from .thread_lifecycle import merge
            result=merge(wb,args.sources,args.into,body,key,dry)
        elif verb in {'note', 'result', 'summary', 'request', 'link', 'state'}:
            target = args.id
            options = {'record_type': verb, 'key': key, 'dry_run': dry}
            if verb in ('note', 'result', 'summary', 'request'): options['refs'] = [wb.citation(x) for x in args.ref]
            if verb == 'request':
                if args.maintenance:
                    target = wb.maintenance_entry()['source']['id']
                recipient = args.to or wb.find(target, 'thread')['doc']['meta']['author']
                if wb.client.get('community_enabled'):
                    from .knowledge import Knowledge
                    person=Knowledge(wb).user(recipient)
                    if person['status']!='active':raise Error('USER_DISABLED','The requested person is disabled.')
                    if not person['name']:raise Error('REAL_NAME_REQUIRED','The recipient must first fill in their real name.')
                    options['recipient']=person['account']
                else:options['recipient'] = recipient
            if verb == 'link': options['refs'] = [wb.ref(wb.find(args.target, 'thread'))]
            if verb == 'state': options['state'] = args.to
            result = wb.append(target, body, **options)
        elif verb == 'correct':
            old = wb.find(args.id, 'entry')
            result = wb.append(old['doc']['meta']['thread'], body, 'correction', supersedes=wb.ref(old), key=key, dry_run=dry)
        else:
            request = wb.find(args.id, 'entry')
            if request['doc']['meta'].get('type') != 'request':
                raise Error('INPUT_INVALID', 'This command takes a request record ID.')
            options = {'request': wb.ref(request), 'key': key, 'dry_run': dry}
            record_type = {'deliver':'delivery'}.get(verb, verb)
            if verb == 'assign': options['work'] = [wb.ref(wb.find(x, 'thread')) for x in args.work]
            if verb in {'deliver', 'adopt'}: options['refs'] = [wb.ref(wb.find(x, 'entry')) for x in args.result]
            if verb == 'deliver': options['complete'] = not args.partial
            result = wb.append(request['doc']['meta']['thread'], body, record_type, **options)
        if publish:
            wb.load()
            result = {'result': result, 'publication': wb.synchronize()}
        return finish(result, reload=verb not in {'status','maintenance','list','overview','locate','inbox','show','context','continue','backlinks','coordinate'} and not publish)
