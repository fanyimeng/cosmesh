"""Candidate personal sharing commands; opt-in and separate from publication."""
from .errors import Error


def add_commands(commands,command):
    group=command(commands,'personal').add_subparsers(dest='personal_command',required=True)
    p=command(group,'init','personal.init');p.add_argument('--device-id')
    command(group,'status','personal.status')
    p=command(group,'connect','personal.connect');p.add_argument('--ssh');p.add_argument('--service')
    command(group,'list','personal.list')
    p=command(group,'join','personal.join');p.add_argument('workspace')
    p=command(group,'sync','personal.sync');p.add_argument('--pull',action='store_true');p.add_argument('--files',action='store_true')
    p=command(group,'policy','personal.policy');p.add_argument('--set',action='append',default=[],metavar='KEY=INTEGER_BYTES')
    assets=command(group,'asset').add_subparsers(dest='asset_command',required=True)
    p=command(assets,'add','personal.asset');p.add_argument('thread');p.add_argument('path');p.add_argument('--science',action='store_true')
    command(assets,'list','personal.asset')
    p=command(assets,'plan','personal.asset');p.add_argument('ids',nargs='*');p.add_argument('--explicit',action='store_true')
    p=command(assets,'push','personal.asset');p.add_argument('ids',nargs='*');p.add_argument('--explicit',action='store_true')
    p=command(assets,'open','personal.asset');p.add_argument('id')


def execute(args):
    from .threads import Workbench,workspace_root
    from .personal import Personal,initialize
    from .personal_protocol import DEFAULTS,integer
    verb=args.personal_command
    offline=getattr(args,'offline',False);dry=getattr(args,'dry_run',False)
    if any(getattr(args,k,None) for k in ('revision','expect_revision','require_fresh','idempotency_key')):
        raise Error('INPUT_INVALID','Personal sharing uses fixed snapshot versions and resumable object identities.')
    network=verb in {'connect','list','join','sync'} or (verb=='asset' and args.asset_command=='push')
    if offline and network: raise Error('OFFLINE','This personal command requires the relay.')
    if dry and (verb not in {'status','policy'} and not (verb=='asset' and args.asset_command in {'list','plan'})):
        raise Error('INPUT_INVALID','Use personal asset plan for transfer previews; this operation does not support --dry-run.')
    if verb=='sync' and args.pull and args.files: raise Error('INPUT_INVALID','--pull never uploads files; omit --files.')
    wb=Workbench(workspace_root(getattr(args,'root',None)))
    with wb.base.locked():
        wb.load()
        if verb=='init':return initialize(wb,args.device_id),None
        personal=Personal(wb)
        if verb=='status':return personal.status(),None
        if verb=='connect':return personal.connect(args.ssh,args.service),None
        if verb=='list':return personal.rpc({'method':'personal_list'}),None
        if verb=='join':return personal.join(args.workspace),None
        if verb=='sync':return personal.sync(args.pull,args.files),None
        if verb=='policy':
            policy=dict(personal.state['policy'])
            for pair in args.set:
                key,sep,value=pair.partition('=')
                if not sep or key not in DEFAULTS:raise Error('INPUT_INVALID','Use a known policy key and positive integer.')
                if key in {'daily_bytes','server_bytes'}:
                    raise Error('POLICY_DENIED','Owner-wide traffic and storage limits are enforced by the relay administrator; local policy cannot change them.')
                try: policy[key]=integer(int(value),2**63-1,1)
                except ValueError:raise Error('INPUT_INVALID','Quota values are positive integers in bytes, or counts for batch_files.')
            if args.set and not dry:personal.state['policy']=policy;personal.save()
            return {'policy':policy,'saved':bool(args.set) and not dry,'server_limits_still_apply':True},None
        action=args.asset_command
        if action=='add':return personal.add(args.thread,args.path,args.science),None
        if action=='list':return {'assets':personal.state['assets']},None
        if action=='plan':return personal.plan(args.ids or None,args.explicit),None
        if action=='push':
            transfers=personal.push(args.ids or None,args.explicit)
            # Publishing the manifest completes handoff; an interrupted commit retries idempotently.
            result=personal.sync();result['transfers']=transfers
            return result,None
        if action=='open':return personal.open(args.id,offline),None
