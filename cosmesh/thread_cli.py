"""Small agent-facing Thread commands, shared by cosmesh and cosbase."""
from pathlib import Path
import os

from .errors import Error
from .sync import hub_receipt
from .threads import Workbench, initialize, workspace_root


def add_commands(commands, command, body):
    group = command(commands, 'thread').add_subparsers(dest='thread_command', required=True)
    from .delivery_cli import add_commands as add_mailbox_commands
    add_mailbox_commands(group, command, body)
    p=command(group,'unshare','thread.unshare');p.add_argument('id',help='Withdraw a historical 0.x share you own')
    find = command(group, 'find', 'thread.find')
    find.add_argument('query', nargs='?', default='')
    init = command(group, 'init', 'thread.init')
    init.add_argument('--user'); init.add_argument('--timezone', default='Asia/Shanghai')
    discover = command(group, 'discover', 'thread.discover')
    discover.add_argument('--ssh'); discover.add_argument('--service')
    p = command(group, 'sync', 'thread.sync'); p.add_argument('id', nargs='?'); p.add_argument('--pull', action='store_true')
    p.add_argument('--files', action='store_true')
    for verb in ('personal', 'fetch', 'locations'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id')
        if verb != 'locations':
            p.add_argument('--ssh'); p.add_argument('--service')
        if verb == 'personal':
            p.add_argument('--files', action='store_true');p.add_argument('--off',action='store_true')
        if verb == 'fetch':
            p.add_argument('--files',action='store_true',help='Restore registered files into the explicit destination directory')
            p.add_argument('--workdir'); p.add_argument('--agent'); p.add_argument('--project-id')
            p.add_argument('--project-name'); p.add_argument('--session-id')
    command(group, 'status', 'thread.status')
    p = command(group, 'locate', 'thread.locate');p.add_argument('--workdir');p.add_argument('--session-id');p.add_argument('--host')
    p.add_argument('--agent');p.add_argument('--project-id');p.add_argument('--project-name')
    p = command(group,'bind-project','thread.bind-project');p.add_argument('id');p.add_argument('--agent',required=True)
    p.add_argument('--project-name');p.add_argument('--project-id');p.add_argument('--host');p.add_argument('--workdir')
    p.add_argument('--kind',choices=('project','workspace','directory'),default='project')
    p.add_argument('--host-file');p.add_argument('--remove',action='store_true')
    p = command(group, 'associate', 'thread.associate'); p.add_argument('id', nargs='?')
    p.add_argument('--missing', action='store_true'); p.add_argument('--host-file'); p.add_argument('--journal')
    p.add_argument('--confirm', metavar='CANDIDATE_ID'); p.add_argument('--expect', metavar='EXECUTION_REVISION')
    p = command(group, 'list', 'thread.list'); p.add_argument('--query'); p.add_argument('--mine', action='store_true');p.add_argument('--all',action='store_true')
    p.add_argument('--personal', action='store_true'); p.add_argument('--remote', action='store_true')
    p.add_argument('--ssh'); p.add_argument('--service')
    p.add_argument('--lifecycle', choices=('active', 'archived', 'trash', 'withdrawn', 'merged', 'all'))
    p.add_argument('--scope', choices=('all','private','public','base','mesh'), default='all')
    p = command(group, 'overview', 'thread.overview'); p.add_argument('id', nargs='?')
    p.add_argument('--mine', action='store_true'); p.add_argument('--query'); p.add_argument('--all', action='store_true')
    p.add_argument('--lifecycle', choices=('active', 'archived', 'trash', 'withdrawn', 'merged', 'all'))
    p.add_argument('--scope', choices=('all', 'private', 'public', 'base', 'mesh'), default='all')
    view = p.add_mutually_exclusive_group()
    view.add_argument('--details', action='store_true'); view.add_argument('--compact', action='store_true')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--host-file', help='Private, bounded metadata from supported host APIs; see guide show threads')
    p.add_argument('--continue', dest='continue_id', help='Read a selected Thread together with the compact private overview')
    p = command(group, 'page', 'thread.page'); p.add_argument('id', nargs='?')
    p.add_argument('--level', choices=('brief', 'status', 'index'), default='brief')
    p.add_argument('--scope', choices=('all', 'private', 'public', 'base', 'mesh'), default='all')
    p.add_argument('--user'); p.add_argument('--query'); p.add_argument('--all', action='store_true')
    p.add_argument('--limit', type=int, default=20); p.add_argument('--offset', type=int, default=0)
    p.add_argument('--refresh', action='store_true')
    presentation = p.add_mutually_exclusive_group()
    presentation.add_argument('--markdown', action='store_true')
    presentation.add_argument('--ready', action='store_true', help='Prepared document and a minimal fixed-source index')
    p.add_argument('--continue', dest='continue_id', help='Include the selected status page with a user overview')
    p = command(group, 'check', 'thread.check'); p.add_argument('id'); p.add_argument('--details',action='store_true')
    p.add_argument('--scope', choices=('all', 'private', 'public', 'base', 'mesh'), default='all')
    p.add_argument('--limit', type=int, default=5)
    p = command(group,'read','thread.read');p.add_argument('id');p.add_argument('--repo',required=True)
    p.add_argument('--offset',type=int,default=0);p.add_argument('--chars',type=int,default=8000)
    p = command(group, 'update', 'thread.update'); p.add_argument('id'); body(p)
    p.add_argument('--kind', choices=('note','result'), default='note'); p.add_argument('--local-only', action='store_true')
    p.add_argument('--basis', help='Optional Thread record-set signature from a previous update receipt.')
    p.add_argument('--ref', action='append', default=[]); p.add_argument('--ready', action='store_true'); p.add_argument('--generation-file')
    brief = p.add_mutually_exclusive_group()
    brief.add_argument('--interaction-summary'); brief.add_argument('--interaction-summary-file')
    for verb in ('show', 'context', 'continue', 'coordinate', 'backlinks'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id')
        if verb=='continue':
            p.add_argument('--ready',action='store_true',help='Read a bounded owner handoff with recent progress and registered local locations')
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
    for verb in ('note','result','summary','correct','link','state','archive','restore','edit','bind'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id')
        if verb != 'bind':
            p.add_argument('--sync', action='store_true')
            p.add_argument('--ready', action='store_true')
        if verb in ('note', 'result', 'summary', 'correct', 'link', 'edit'):
            body(p)
        if verb in {'note','result','summary','correct'}:
            p.add_argument('--title', help='Complete short title, independent of the interaction summary')
        if verb in ('note', 'result', 'summary'):
            p.add_argument('--ref', action='append', default=[], help='Cite ID or pinned ID@VERSION; repeat for multiple sources')
        if verb in ('note','result','summary','edit','correct','state','archive','restore'):
            p.add_argument('--generation-file')
        if verb in ('note','result','summary','correct','state','archive','restore'):
            brief = p.add_mutually_exclusive_group()
            brief.add_argument('--interaction-summary');brief.add_argument('--interaction-summary-file')
        if verb == 'link':
            p.add_argument('--target', required=True)
        if verb == 'state':
            p.add_argument('--to', choices=('active', 'done', 'cancelled', 'archived'), required=True)
        if verb == 'edit':
            p.add_argument('--expect', required=True); p.add_argument('--title')
        if verb == 'bind':
            p.add_argument('--agent', required=True); p.add_argument('--workdir', required=True)
            p.add_argument('--session-id'); p.add_argument('--host')


def execute(args):
    from .access import system_user
    from .cli import read_input
    from .protocol import MAX_DOCUMENT
    verb = args.thread_command
    root = workspace_root(getattr(args, 'root', None))
    dry, offline = getattr(args, 'dry_run', False), getattr(args, 'offline', False)
    fresh, publish = getattr(args, 'require_fresh', False), getattr(args, 'sync', False)
    if publish and verb!='sync':
        raise Error('COMMAND_RETIRED','Save the record locally, then use thread personal ID for owner synchronization.')
    if getattr(args,'local_only',False) and publish:
        raise Error('INPUT_INVALID', 'Choose --local-only or --sync.')
    if getattr(args, 'revision', None) or getattr(args, 'expect_revision', None):
        raise Error('INPUT_INVALID', 'Thread contexts carry separate source versions; use pinned references or edit --expect.')
    if offline and (fresh or publish or verb in {'discover', 'sync'}):
        raise Error('INPUT_INVALID', 'Offline Thread operations cannot contact the Hub.')
    if dry and (publish or fresh or verb in {'discover', 'sync'}):
        raise Error('INPUT_INVALID', 'Dry-run previews local changes and cannot synchronize.')
    if dry and verb == 'associate':
        raise Error('INPUT_INVALID', 'Association previews are read-only without --dry-run; confirmation is an explicit write.')
    if fresh and verb not in {'find', 'list', 'overview', 'page', 'check', 'chat', 'show', 'context', 'continue', 'coordinate', 'backlinks', 'inbox', 'outbox', 'mail', 'activity', 'search', 'status'}:
        raise Error('INPUT_INVALID', '--require-fresh applies to reads and requires a verifiable source.')
    if verb == 'overview' and ((args.scope == 'mesh' and args.host_file) or (args.scope == 'base' and fresh)):
        raise Error('INPUT_INVALID', 'Public overview excludes host metadata; private overview stays local without --require-fresh.')
    if verb == 'overview' and args.continue_id and (not args.compact or args.scope == 'mesh'):
        raise Error('INPUT_INVALID', 'Use --compact and a private scope for combined continuation.')
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if prefix and (root == Path(prefix).resolve() or Path(prefix).resolve() in root.parents):
        raise Error('PATH_INVALID', 'Keep the workbench outside the application installation.')
    if verb == 'sync' and not args.id and Workbench(root).retired_sync():
        from .sync import retired_transport
        args._thread_sync_state = {'state': 'transport_retired', 'identity_verified': False}
        raise retired_transport()
    if verb=='unshare':
        from .thread_access_cli import Online
        if offline or dry:
            raise Error('INPUT_INVALID','Withdrawing an old share requires online execution.')
        online=Online(Workbench(root))
        row=online.call('thread_access_status',thread=args.id)
        result=online.call('thread_access_set',thread=args.id,visibility='private',readers=[],
                           sha=row['content_revision'],expect=row['acl_revision'])
        args._thread_sync_state=hub_receipt()
        return result,None
    if verb=='find' or (verb=='list' and not (args.personal or args.remote) and args.scope=='all' and not args.lifecycle and not args.all):
        from .work_directory import lookup
        wb=Workbench(root)
        result=lookup(wb,args.query or '',offline,fresh or getattr(args,'remote',False))
        if result['scope']=='local_and_current_hub':args._thread_sync_state=hub_receipt()
        else:args._thread_sync_state={'state':'local_only','identity_verified':False}
        return result,None
    if verb in {'personal', 'fetch', 'locations'} or (verb == 'sync' and args.id) or (verb == 'list' and (args.personal or args.remote)):
        from .thread_personal_cli import execute as execute_personal_thread
        args._thread_sync_state = {'state': 'local_only', 'identity_verified': False}
        result = execute_personal_thread(args, root)
        if verb != 'locations' and not getattr(args,'off',False):
            args._thread_sync_state = hub_receipt()
        return result
    if verb in {'send','request','reply','inbox','outbox','mail','chat','search','activity'}:
        from .delivery_cli import execute as messages
        result=messages(args,Workbench(root))
        if not (dry or getattr(args,'local_only',False)):args._thread_sync_state=hub_receipt()
        return result,None
    if verb == 'sync' and args.files:
        raise Error('INPUT_INVALID', '--files requires a selected Thread ID.')
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
            if not wb.client.get('actor') or result.get('actor')!=wb.client.get('actor'):raise Error('AUTH_DENIED','Source read identity differs.')
            args._thread_sync_state = hub_receipt()
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
    if verb in {'page','check'}:
        args._thread_sync_state = {'state': 'local_only', 'identity_verified': False}
        if dry or (args.scope == 'base' and fresh) or (args.id and args.continue_id):
            raise Error('INPUT_INVALID', 'Status pages are local derived reads; omit --dry-run and keep private scope offline.')
        if fresh:
            with wb.base.locked():
                wb.load()
                if wb.synchronize(publish=False)['errors']:
                    raise Error('FRESHNESS_UNAVAILABLE', 'Some shared sources could not be refreshed; cached sources are retained.')
        from .status_pages import read_page
        result,revision=read_page(wb,args)
        return result,revision
    with wb.base.locked():
        wb.load()
        if verb=='continue' and args.ready:
            from .thread_lifecycle import continuation
            args._thread_sync_state={'state':'local_only','identity_verified':False}
            result=continuation(wb,args.id,ready=True,fresh=fresh)
            if fresh:args._thread_sync_state=hub_receipt()
            return result,wb._revisions['local']
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
            if not dry and not publish and verb not in {'status','sync','maintenance','list','overview','locate','inbox','show','context','continue','backlinks','coordinate'} and not (verb == 'associate' and not args.confirm):
                from .status_pages import schedule
                schedule(wb)
            if getattr(args,'ready',False) and verb not in {'overview','inbox','update'} and result.get('format') != 'thread.send.v1':
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
            result = finish(wb.discover(args.ssh, args.service))
            if result[0].get('mode') == 'thread_access':
                args._thread_sync_state = hub_receipt()
            return result
        if verb == 'sync':
            raise Error('COMMAND_RETIRED','Select a Thread ID to synchronize.')
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
        if verb == 'update':
            from .thread_updates import update as project_update
            return finish(project_update(wb,args,body,key,dry))
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
        elif verb == 'associate':
            import json
            if args.missing and (args.id or args.host_file or args.journal or args.confirm or args.expect):
                raise Error('INPUT_INVALID', 'Use --missing alone, or select one Thread for an association preview.')
            if not args.missing and not args.id:
                raise Error('INPUT_INVALID', 'Select a Thread or use --missing.')
            if args.confirm and (args.missing or not args.host_file or not args.expect):
                raise Error('INPUT_INVALID', 'Confirmation needs the original --host-file and its --expect execution revision.')
            if args.expect and not args.confirm:
                raise Error('INPUT_INVALID', '--expect applies only to an explicit association confirmation.')
            from .thread_execution_associate import confirm, missing, preview, read_journal
            if args.missing:
                result = missing(wb)
            else:
                host = json.loads(read_input(args.host_file, MAX_DOCUMENT)) if args.host_file else None
                journal = read_journal(args.journal) if args.journal else None
                if args.confirm:
                    result = confirm(wb, args.id, args.confirm, args.expect, host, key, journal)
                else:
                    result = preview(wb, args.id, host, journal)
        elif verb == 'list':
            result = []
            for item in wb.catalog.values():
                doc = item['doc']
                if doc['meta']['kind'] != 'thread' or (args.mine and doc['meta']['author'] != wb.author_for(item)):
                    continue
                if args.query and args.query.casefold() not in (doc['meta']['title'] + '\n' + doc['body']+'\n'+'\n'.join(wb._thread_names[doc['meta']['id']]['aliases'])).casefold():
                    continue
                if (args.scope == 'base' and item['repo'] != 'local') or (args.scope == 'mesh' and item['repo'] == 'local'): continue
                shown=wb.present(item)
                placement = args.lifecycle or ('all' if args.all else 'active')
                if placement != 'all' and shown['lifecycle'] != placement:continue
                if args.scope != 'mesh':
                    from .thread_execution_reader import owner_execution
                    shown['execution_metadata'] = owner_execution(wb, doc['meta']['id'])
                else:
                    from .thread_execution_reader import shared_execution, shared_execution_cache
                    metadata = shared_execution(wb, doc['meta']['id'])
                    if metadata:
                        shown['execution_metadata'] = metadata
                        shown['execution_cache'] = shared_execution_cache(wb, doc['meta']['id'])
                result.append(shown)
            result.sort(key=lambda x:(x['created_at'], x['id']), reverse=True)
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
        elif verb in {'note', 'result', 'summary', 'link', 'state'}:
            target = args.id
            options = {'record_type': verb, 'key': key, 'dry_run': dry, 'title':getattr(args,'title',None)}
            if verb in ('note', 'result', 'summary'): options['refs'] = [wb.citation(x) for x in args.ref]
            if verb == 'link': options['refs'] = [wb.ref(wb.find(args.target, 'thread'))]
            if verb == 'state': options['state'] = args.to
            result = wb.append(target, body, **options)
        elif verb == 'correct':
            old = wb.find(args.id, 'entry')
            result = wb.append(old['doc']['meta']['thread'], body, 'correction', supersedes=wb.ref(old), key=key, dry_run=dry, title=args.title)
        else:raise Error('COMMAND_RETIRED','Use the current Thread commands; retained historical records remain readable.')
        if verb in {'bind', 'bind-project'} and not dry:
            from .thread_execution_reader import owner_execution
            result['execution_metadata'] = owner_execution(wb, args.id)
        return finish(result, reload=verb not in {'status','maintenance','list','overview','locate','inbox','show','context','continue','backlinks','coordinate'} and not publish)
