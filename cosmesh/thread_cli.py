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
    p = command(group, 'inbox', 'thread.inbox'); p.add_argument('--sent', action='store_true'); p.add_argument('--all', action='store_true')
    for verb in ('show', 'context', 'continue', 'coordinate', 'backlinks'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id')
    p = command(group, 'create', 'thread.create'); p.add_argument('--title', required=True); body(p)
    p.add_argument('--generation-file')
    p = command(group, 'split', 'thread.split'); p.add_argument('id'); p.add_argument('--title', required=True); body(p)
    p.add_argument('--ref', action='append', default=[]);p.add_argument('--sync', action='store_true')
    p = command(group, 'merge', 'thread.merge'); p.add_argument('sources', nargs='+');p.add_argument('--into',required=True)
    body(p);p.add_argument('--sync',action='store_true')
    p = command(group, 'delete', 'thread.delete');p.add_argument('id');body(p);p.add_argument('--sync',action='store_true')
    for verb in ('note', 'result', 'request', 'reply', 'assign', 'decline', 'deliver', 'adopt', 'cancel', 'correct', 'link', 'state', 'archive','restore','share', 'edit', 'bind'):
        p = command(group, verb, 'thread.' + verb); p.add_argument('id', **({'nargs':'?'} if verb == 'request' else {}))
        if verb != 'bind':
            p.add_argument('--sync', action='store_true')
        if verb in ('note', 'result', 'request', 'reply', 'assign', 'decline', 'deliver', 'adopt', 'cancel', 'correct', 'link', 'edit'):
            body(p)
        if verb == 'request':
            p.add_argument('--to', help='Recipient account, stable ID or real name; defaults to the Thread creator')
            p.add_argument('--maintenance', action='store_true', help='Use the Hub maintenance Thread')
        if verb in ('note', 'result', 'request'):
            p.add_argument('--ref', action='append', default=[], help='Cite ID or pinned ID@VERSION; repeat for multiple sources')
        if verb in ('note','result','edit'):p.add_argument('--generation-file')
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
    if fresh and verb not in {'list', 'overview', 'show', 'context', 'continue', 'coordinate', 'backlinks', 'inbox', 'status', 'maintenance'}:
        raise Error('INPUT_INVALID', '--require-fresh applies to reads and only pulls shared content.')
    if verb == 'overview' and ((args.scope == 'mesh' and args.host_file) or (args.scope == 'base' and fresh)):
        raise Error('INPUT_INVALID', 'Mesh overview excludes host metadata; Base overview stays local without --require-fresh.')
    if verb == 'overview' and args.continue_id and (not args.compact or args.scope == 'mesh'):
        raise Error('INPUT_INVALID', 'Use --compact and a private scope for combined continuation.')
    if verb == 'request' and bool(args.id) == args.maintenance:
        raise Error('INPUT_INVALID', 'Choose a Thread ID or --maintenance.')
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if prefix and (root == Path(prefix).resolve() or Path(prefix).resolve() in root.parents):
        raise Error('PATH_INVALID', 'Keep the workbench outside the application installation.')
    if verb == 'init':
        return initialize(root, args.user or system_user()['user'], args.timezone, dry)
    if verb == 'discover' and not root.exists():
        initialize(root, system_user()['user'])
    wb = Workbench(root)
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
        if verb == 'status':
            result = wb.status()
        elif verb == 'maintenance':
            result = wb.maintenance_entry()
        elif verb == 'locate':
            from .thread_lifecycle import locate
            if not args.workdir and not args.session_id:
                raise Error('INPUT_INVALID', 'Use the actual current workdir or Session ID.')
            result = locate(wb,args.workdir,args.session_id,args.host)
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
                if args.query and args.query.casefold() not in (doc['meta']['title'] + '\n' + doc['body']).casefold():
                    continue
                shown=wb.present(item)
                placement = args.lifecycle or ('all' if args.all else 'active')
                if placement != 'all' and shown['lifecycle'] != placement:continue
                result.append(shown)
            result.sort(key=lambda x:(x['created_at'], x['id']), reverse=True)
        elif verb == 'inbox':
            result = []
            for item in wb.catalog.values():
                meta = item['doc']['meta']
                if meta.get('type') == 'request' and meta['author' if args.sent else 'to'] == wb.user:
                    state = wb.request_state(item)
                    if args.all or state['state'] not in {'declined', 'adopted', 'cancelled'}:
                        parent = wb.find(meta['thread'], 'thread')
                        result.append({'request': wb.present(item), 'thread_lifecycle': wb.present(parent)['lifecycle'], **state})
            result.sort(key=lambda x:x['request']['created_at'], reverse=True)
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
            result = wb.create(args.title, body, key, dry)
        elif verb == 'share':
            result = wb.share(args.id, key, dry)
        elif verb == 'edit':
            result = wb.edit(args.id, args.expect, args.title, body if hasattr(args, 'body') or hasattr(args, 'file') else None, key, dry)
        elif verb == 'bind':
            result = wb.bind(args.id, args.agent, args.workdir, args.session_id, args.host, dry)
        elif verb in {'archive','restore','delete'}:
            from .thread_lifecycle import placement
            result=placement(wb,args.id,verb,body,key,dry)
        elif verb == 'split':
            from .thread_lifecycle import split
            result=split(wb,args.id,args.title,body,[wb.citation(x) for x in args.ref],key,dry)
        elif verb == 'merge':
            from .thread_lifecycle import merge
            result=merge(wb,args.sources,args.into,body,key,dry)
        elif verb in {'note', 'result', 'request', 'link', 'state'}:
            target = args.id
            options = {'record_type': verb, 'key': key, 'dry_run': dry}
            if verb in ('note', 'result', 'request'): options['refs'] = [wb.citation(x) for x in args.ref]
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
