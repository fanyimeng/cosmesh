"""Thread-first private relay commands; no whole-store join or upload."""
from .errors import Error
from .personal import initialize
from .thread_personal import ThreadPersonal
from .thread_execution import ThreadExecution
from .threads import Workbench


def brief_receipt(result):
    restoring = result.get('restored', False)
    files = result.get('files') if restoring else result.get('transfers')
    status = 'synced' if result.get('uploaded') else 'pulled'
    receipt = {'thread': result['thread'], 'status': 'restored' if restoring else status, 'complete': True}
    missing = result.get('missing_private_references', [])
    if missing:
        receipt['external_references'] = len(missing)
    if result.get('destination'):
        receipt['destination'] = result['destination']
    if files is None or (restoring and not files.get('requested')):
        return receipt
    rows = files['files']
    good = 'available' if restoring else 'server_available'
    issues = [r for r in rows if r['state'] != good]
    unchanged = sum(bool(r.get('unchanged')) for r in rows if r['state'] == good)
    receipt['files'] = {'updated': len(rows) - len(issues) - unchanged, 'unchanged': unchanged,
                        'skipped': len(issues)}
    if issues:
        receipt.update(complete=False, status='partial',
                       issues=[{'file': r.get('relative_path') or r.get('name'), 'reason': r['reason']}
                               for r in issues[:5]])
    return receipt


def execute(args, root):
    verb = args.thread_command
    if getattr(args, 'dry_run', False) or getattr(args, 'require_fresh', False) or getattr(args, 'idempotency_key', None):
        raise Error('INPUT_INVALID', 'Thread personal sync uses fixed manifest versions; omit dry-run, require-fresh and operation keys.')
    if getattr(args, 'offline', False) and verb != 'locations' and not getattr(args,'off',False):
        raise Error('OFFLINE', 'This operation requires your private relay.')
    if verb == 'list' and (not args.personal or not args.remote or args.scope != 'all' or args.mine or args.all or args.lifecycle):
        raise Error('INPUT_INVALID', 'Use thread list --personal --remote [--query TEXT] for the private server index.')
    if not root.exists() and verb in {'fetch', 'list'}:
        from .threads import initialize as init_work
        from .access import system_user
        init_work(root, system_user()['user'])
    wb = Workbench(root)
    with wb.base.locked():
        wb.load()
        if verb=='personal' and getattr(args,'off',False):
            from .collector import enable_sync,owned
            tid=owned(wb,args.id);enable_sync(wb,tid,False)
            return {'thread':tid,'personal_sync':'off','previous_snapshot_retained':True},wb.base.head
        if verb == 'locations' and not wb.base.path('.cosmesh/personal-v1/state.json').exists():
            item = wb.find(args.id, 'thread'); tid = item['doc']['meta']['id']
            from .thread_execution_reader import owner_execution
            return {'thread': tid, 'private': True, 'sharing': 'local' if item['repo'] == 'local' else 'public', 'personal_sync': 'off',
                    'execution_metadata': owner_execution(wb, tid),
                    'bindings': [r for r in wb.client['bindings'] if r['thread'] == tid],
                    'project_bindings': [r for r in wb.client.get('project_bindings', []) if r['thread'] == tid]}, wb.base.head
        initialize(wb)
        personal = ThreadPersonal(wb)
        if verb != 'locations' and (not personal.state['connection'] or getattr(args, 'ssh', None) or getattr(args, 'service', None)):
            personal.connect(getattr(args, 'ssh', None), getattr(args, 'service', None))
        if verb == 'list':
            info = personal.capability()
            result = personal.rpc({'method': 'personal_thread_list'})
            rows = {r['thread']: dict(r, sharing='personal', personal_sync='thread') for r in result['threads']}
            if info.get('execution_protocol') == 'thread.execution.v1' and not info.get('unified'):
                for row in personal.rpc({'method': 'personal_execution_list'})['threads']:
                    rows[row['thread']] = dict(row, sharing='public', personal_sync='execution')
            result['threads'] = [rows[k] for k in sorted(rows)]
            if args.query: result['threads'] = [r for r in result['threads'] if args.query.casefold() in r['title'].casefold()]
            from .thread_execution_reader import owner_execution
            for row in result['threads']:
                row['execution_metadata'] = owner_execution(wb, row['thread'])
        elif verb == 'fetch':
            known = wb.catalog.get(args.id)
            info = personal.capability()
            if info.get('unified'):
                # Keep old caches on disk for offline history/rollback, detach their
                # live catalog entries before importing an owner's canonical Thread.
                try:online=personal.rpc({'method':'thread_access_status','thread':args.id})
                except Error as exc:
                    if exc.code!='NOT_FOUND':raise
                    online={'writable':True}
                if not online.get('writable'):raise Error('AUTH_DENIED','Use thread browse for another owner; only your own Thread is restored.')
                wb.client.setdefault('retired_spaces',{}).update(wb.client['spaces'])
                wb.client['spaces']={};wb.save_client();wb.load();known=wb.catalog.get(args.id)
            execution = info.get('execution_protocol') == 'thread.execution.v1' and personal.rpc(
                {'method': 'personal_execution_head', 'thread': args.id})['sha']
            if not info.get('unified') and (execution or (known and known['repo'] != 'local')):
                personal = ThreadExecution(wb)
            result = personal.restore(args.id, args.workdir, args.agent, args.project_id, args.project_name, args.session_id, args.files)
        else:
            tid = wb.find(args.id, 'thread')['doc']['meta']['id']
            if wb.find(tid)['repo'] != 'local' and not personal.capability().get('unified'):
                personal = ThreadExecution(wb)
            if verb == 'locations': return personal.locations(tid), wb.base.head
            result = personal.synchronize(tid, getattr(args, 'pull', False), args.files)
        if verb in {'personal','fetch'}:
            from .collector import enable_sync
            enable_sync(wb,result['thread'],True)
        if getattr(args, 'brief', False):
            result = brief_receipt(result)
        return result, wb.base.head
