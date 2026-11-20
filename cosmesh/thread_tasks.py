"""Owner-only task delivery. A durable submit fence always precedes a native model turn."""
from pathlib import Path
import socket
import sys
import time

from .collector import owned, policy_dir
from .errors import Error
from .personal import Personal
from .personal_protocol import read_json
from .task_queue import MAX_BODY, result_body
from .thread_execution_model import normalize_agent
from .thread_personal import ThreadPersonal
from .thread_protocol import identifier


def commands(group, command, body):
    actions = command(group, 'task').add_subparsers(dest='task_action', required=True)
    for verb in ('send', 'targets', 'list', 'receive', 'resolve'):
        p = command(actions, verb, 'thread.task.' + verb)
        p.add_argument('id', help='Personal-sync Thread ID')
        if verb in ('send','receive','resolve'):
            p.add_argument('--agent', choices=('codex','dsh'), required=True)
            p.add_argument('--session', required=verb != 'send')
        if verb == 'send':
            p.add_argument('--device', required=True, help='Exact registered hostname or device ID')
            body(p)
        if verb == 'receive':
            p.add_argument('--once', action='store_true')
            p.add_argument('--interval', type=int, default=15)
            p.add_argument('--socket', help='Socket of the existing Codex app-server')
            p.add_argument('--dsh-stdio', action='store_true', help='Internal connector transport')
        if verb == 'resolve':
            p.add_argument('--task-id', required=True)
            p.add_argument('--outcome', required=True, choices=('failed','completed'))
            body(p)


def enabled(wb, tid):
    policy = read_json(policy_dir(wb) / tid)
    if not (policy and policy.get('enabled')):
        raise Error('PERSONAL_NOT_ENABLED', 'Enable personal sync for this Thread on this device first.')


def rpc(personal, verb, **p):
    try:
        return personal.rpc(dict(method='personal_task_' + verb, **p))
    except Error as exc:
        if exc.code == 'INPUT_INVALID' and 'Invalid personal relay request' in exc.message:
            raise Error('FEATURE_UNAVAILABLE', 'Task dispatch requires CosMesh Hub 0.6.2 or newer.') from exc
        raise


def run_once(wb, tid, agent, session, host, native, resolve=None):
    wb.client = wb.read_client()
    enabled(wb, tid)
    personal = Personal(wb)
    dirs = {b['workdir'] for b in wb.client['bindings'] if b['thread'] == tid and b['host'] == host
            and normalize_agent(b['agent']) == agent and b.get('session_id') == session}
    if len(dirs) != 1:
        raise Error('TARGET_CHANGED', 'Select a locally bound native session with one working directory.')
    route = dict(thread=tid, host=host, agent=agent, session=session,
                 device=personal.state['device'], replica=personal.state['replica'])
    task = rpc(personal, 'next', **route)['task']
    if resolve and (not task or task['id'] != resolve['id'] or task['status'] != 'unconfirmed'):
        raise Error('STATE_CONFLICT', 'Only the original receiver can resolve an unconfirmed task.')
    if not task:
        return {'state':'idle'}
    route['id'] = task['id']
    args = dict(session=session, workdir=dirs.pop(), task=task)
    if task['status'] == 'queued':
        if not Path(args['workdir']).is_dir():
            raise Error('PATH_INVALID', 'The bound working directory is unavailable.')
        host_state = native.call('inspect', **args)
        if not host_state['ready']:
            return {'state':'busy','task':task['id']}
        with wb.base.locked():
            wb.load()
            enabled(wb, tid)
            owned(wb, tid)
            ThreadPersonal(wb).synchronize(tid, pull=True)
        if rpc(personal, 'claim', **route, cursor=host_state['cursor'])['claimed']:
            native.call('start', **args)
        return {'state':'submitted', 'task':task['id']}
    if task['status'] in ('submitted','unconfirmed'):
        result = resolve or native.call('observe', **args)
        if result['state'] == 'unconfirmed' and task['status'] != 'unconfirmed':
            rpc(personal, 'uncertain', **route)
        if result['state'] not in ('completed','failed'):
            return {'state':result['state'], 'task':task['id']}
        rpc(personal, 'report', **route, outcome=result['state'], result=result['result'])
        task.update(outcome=result['state'], result=result['result'])
    with wb.base.locked():
        wb.load()
        enabled(wb, tid)
        owned(wb, tid)
        sync = ThreadPersonal(wb)
        record = wb.append(tid, result_body(task), record_type='result', title='定向任务结果',
                           key='task-result-' + task['id'])['id']
        wb.load()
        sync.synchronize(tid)
    return rpc(personal, 'finish', **route, record=record)


def execute(args, wb):
    from .cli import read_input
    from .task_hosts import Codex, Dsh
    if getattr(args,'offline',False) or getattr(args,'dry_run',False):
        raise Error('INPUT_INVALID', 'Task operations require the personal Hub online.')
    tid = identifier(args.id, 'W')
    verb = args.task_action
    enabled(wb, tid)
    body = read_input(args.file, MAX_BODY) if hasattr(args,'file') else getattr(args,'body','')
    if verb in ('send','targets','list'):
        personal = Personal(wb)
        if verb != 'send':
            return rpc(personal,verb,thread=tid)
        key = getattr(args,'idempotency_key',None)
        if not key:
            raise Error('INPUT_INVALID','Supply --idempotency-key for this logical assignment and reuse it on retries.')
        return rpc(personal,'send',thread=tid,device=args.device,agent=args.agent,session=args.session,key=key,body=body)
    endpoint = (wb, tid, args.agent, args.session, socket.gethostname())
    if verb == 'resolve':
        return run_once(*endpoint,None,resolve=dict(id=args.task_id,state=args.outcome,result=body))
    if not 1 <= args.interval <= 3600 or (args.agent == 'dsh') != args.dsh_stdio or (args.dsh_stdio and args.socket):
        raise Error('INPUT_INVALID', 'Use the DSH connector or a Codex receiver with a 1–3600 second interval.')
    native = Dsh() if args.dsh_stdio else Codex(args.socket)
    last_state = None
    try:
        while True:
            try:
                receipt = run_once(*endpoint,native)
                if args.once:
                    return receipt
                progress = receipt.get('state') or receipt.get('status')
                if progress != last_state and progress not in ('idle','busy'):
                    print('CosMesh task receiver: ' + progress, file=sys.stderr, flush=True)
                last_state = progress
            except (Error, OSError, ValueError) as exc:
                if args.once:
                    raise
                state = getattr(exc, 'code', type(exc).__name__)
                if state != last_state:
                    print('CosMesh task receiver: ' + state, file=sys.stderr, flush=True)
                last_state = state
            if args.dsh_stdio:
                import select
                if select.select([sys.stdin.buffer], [], [], args.interval)[0]:
                    return {'state':'connector_disconnected'}
            else:
                time.sleep(args.interval)
    finally:
        if isinstance(native, Codex):
            native.close()
