"""Candidate personal sharing commands; opt-in and separate from publication."""
from .errors import Error


def add_commands(commands,command):
    group=command(commands,'personal').add_subparsers(dest='personal_command',required=True,metavar='{status,policy,asset}')
    # Old spellings are inert migration stubs, not a second command implementation.
    import argparse
    for verb in ('init','connect','list','join','sync'):
        p=command(group,verb,'personal.'+verb);p.add_argument('retired_args',nargs=argparse.REMAINDER)
        for flag in ('--pull','--files'):p.add_argument(flag,action='store_true')
        for flag in ('--device-id','--ssh','--service'):p.add_argument(flag)
    command(group,'status','personal.status')
    p=command(group,'policy','personal.policy');p.add_argument('--set',action='append',default=[],metavar='KEY=INTEGER_BYTES')
    assets=command(group,'asset').add_subparsers(dest='asset_command',required=True,metavar='{add,list,plan,open}')
    p=command(assets,'add','personal.asset');p.add_argument('thread');p.add_argument('path');p.add_argument('--science',action='store_true')
    p.add_argument('--as',dest='relative_path',help='Portable relative destination inside the selected Thread directory')
    command(assets,'list','personal.asset')
    p=command(assets,'plan','personal.asset');p.add_argument('ids',nargs='*');p.add_argument('--explicit',action='store_true')
    p=command(assets,'push','personal.asset');p.add_argument('ids',nargs='*');p.add_argument('--explicit',action='store_true')
    p=command(assets,'open','personal.asset');p.add_argument('id')


def execute(args):
    from .threads import Workbench,workspace_root
    from .personal import Personal,initialize
    from .personal_protocol import DEFAULTS,integer
    verb=args.personal_command
    if verb in {'init','connect','list','join','sync'}:
        raise Error('COMMAND_RETIRED', 'Whole-store personal commands are retired. Use thread personal ID, thread list --personal --remote, thread fetch ID, or thread sync ID. Existing data is retained; see guide show personal.')
    if verb=='asset' and args.asset_command=='push':
        raise Error('COMMAND_RETIRED','Use thread sync THREAD_ID --files to transfer only selected Thread assets. File references are retained.')
    offline=getattr(args,'offline',False);dry=getattr(args,'dry_run',False)
    if any(getattr(args,k,None) for k in ('revision','expect_revision','require_fresh','idempotency_key')):
        raise Error('INPUT_INVALID','Personal sharing uses fixed snapshot versions and resumable object identities.')
    if dry and (verb not in {'status','policy'} and not (verb=='asset' and args.asset_command in {'list','plan'})):
        raise Error('INPUT_INVALID','Use personal asset plan for transfer previews; this operation does not support --dry-run.')
    wb=Workbench(workspace_root(getattr(args,'root',None)))
    with wb.base.locked():
        wb.load()
        if verb=='asset' and args.asset_command=='add': initialize(wb)
        personal=Personal(wb)
        if verb=='status':
            from .personal_protocol import read_json
            return {'audience':'owner','device':personal.state['device'],
                    'connected':bool(personal.state['connection']),
                    'threads':read_json(personal.root/'threads/index.json',{}),
                    'execution_threads':sorted(p.stem for p in (personal.root/'executions').glob('W-*.json')),
                    'legacy_snapshot_retained':bool(personal.state['base']),
                    'automatic_background':False},None
        if verb=='policy':
            from .transfer_policy import effective
            policy=effective(personal.state['policy'],personal.state.get('policy_overrides',[]))
            for pair in args.set:
                key,sep,value=pair.partition('=')
                if not sep or key not in DEFAULTS:raise Error('INPUT_INVALID','Use a known policy key and positive integer.')
                if key in {'daily_bytes','server_bytes'}:
                    raise Error('POLICY_DENIED','Owner-wide traffic and storage limits are enforced by the relay administrator; local policy cannot change them.')
                try: policy[key]=integer(int(value),2**63-1,1)
                except ValueError:raise Error('INPUT_INVALID','Quota values are positive integers in bytes, or counts for batch_files.')
            if args.set and not dry:
                personal.state['policy']=policy
                personal.state['policy_overrides']=sorted(set(personal.state.get('policy_overrides',[]))|{p.split('=',1)[0] for p in args.set});personal.save()
            return {'policy':policy,'saved':bool(args.set) and not dry,'server_limits_still_apply':True},None
        action=args.asset_command
        if action=='add':return personal.add(args.thread,args.path,args.science,args.relative_path),None
        if action=='list':return {'assets':personal.state['assets']},None
        if action=='plan':return personal.plan(args.ids or None,args.explicit),None
        if action=='open':return personal.open(args.id,offline),None
