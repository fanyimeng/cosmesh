"""Stable JSON commands for agents; human-readable output uses the same envelope."""

import argparse
import getpass
import importlib.metadata
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

from . import __version__
from .connections import networked
from .errors import Error
from .protocol import KINDS, MAX_DOCUMENT
from .service import READS, Service, initialize
from .skills import AGENTS, inspect, install
from .store import Store
from .hub import Hub
from .sync import clone, configure, state, synchronize
from .profile import FIELDS as PROFILE_FIELDS, PersonalProfile, MAX_PROFILE
from .feedback import Feedback, CATEGORIES, PRODUCTS, MAX_BODY
from . import guide
from . import field_crypto
from .spaces import root_for


def installation():
    prefix = os.environ.get("COSMESH_INSTALL_PREFIX")
    if prefix:
        return {"mode": "managed_release", "prefix": prefix}
    try:
        direct = importlib.metadata.distribution("cosmesh").read_text("direct_url.json")
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            return {"mode": "development", "source": str(Path(__file__).resolve().parent.parent)}
    except importlib.metadata.PackageNotFoundError:
        return {"mode": "development", "source": str(Path(__file__).resolve().parent.parent)}
    if (Path(__file__).resolve().parent.parent / "pyproject.toml").is_file():
        return {"mode": "development", "source": str(Path(__file__).resolve().parent.parent)}
    return {"mode": "wheel"}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Error("INPUT_INVALID", message, "Use --help or capabilities to inspect the available commands.")


def parser(product):
    common = Parser(add_help=False)
    for name, options in (
        ("--root", {"help": "Explicit managed data directory"}),
        ("--json", {"action": "store_true"}),
        ("--dry-run", {"action": "store_true"}),
        ("--revision", {"help": "Read a complete historical Git commit"}),
        ("--expect-revision", {"help": "Expected current Git revision"}),
        ("--idempotency-key", {"help": "Stable key for one logical request"}),
        ("--require-fresh", {"action": "store_true"}),
        ("--offline", {"action": "store_true"}),
    ):
        common.add_argument(name, default=argparse.SUPPRESS, **options)
    root = Parser(prog=product, description="CosMesh / CosBase document client and controlled SSH Hub", parents=[common])
    version_label = __version__ + (" (development)" if installation()["mode"] == "development" else "")
    root.add_argument("--version", action="version", version=version_label)
    commands = root.add_subparsers(dest="command", required=True)

    def command(group, name, action=None):
        p = group.add_parser(name, parents=[common])
        if action:
            p.set_defaults(action=action)
        return p

    def body(p):
        inputs = p.add_mutually_exclusive_group()
        inputs.add_argument("--body", default=argparse.SUPPRESS)
        inputs.add_argument("--file", default=argparse.SUPPRESS)

    def dates(p):
        for key in ("due", "followup"):
            g = p.add_mutually_exclusive_group()
            g.add_argument("--" + key, default=argparse.SUPPRESS)
            g.add_argument("--clear-" + key, dest=key, action="store_const", const=None, default=argparse.SUPPRESS)

    def edit_fields(p):
        p.add_argument("--title", default=argparse.SUPPRESS)
        p.add_argument("--project", default=argparse.SUPPRESS)
        p.add_argument("--next", dest="next_action", default=argparse.SUPPRESS)
        p.add_argument("--waiting", default=argparse.SUPPRESS)
        p.add_argument("--triaged", dest="needs_triage", action="store_false", default=argparse.SUPPRESS)
        body(p)
        dates(p)

    def listing(p, kind=None):
        if kind:
            p.set_defaults(kind=kind)
        else:
            p.add_argument("--kind", choices=KINDS)
        p.add_argument("--lifecycle", choices=("active", "archived", "trash", "all"), default="active")
        p.add_argument("--query")
        p.add_argument("--project")
        p.add_argument("--waiting", action="store_true")

    init = command(commands, "init", "init")
    init.add_argument("--user", help='Defaults to the current operating-system account')
    init.add_argument("--timezone", help='New-space default: personal profile, then Asia/Shanghai')
    guides = command(commands, 'guide').add_subparsers(dest='guide_command', required=True)
    command(guides, 'list', 'guide.list')
    gs = command(guides, 'search', 'guide.search')
    gs.add_argument('query')
    gs.add_argument('--limit', type=int, default=8)
    get = command(guides, 'show', 'guide.show')
    get.add_argument('topic', choices=tuple(guide.TOPICS))
    get.add_argument('--section', type=int)
    legacy_feedback = command(commands, 'feedback')
    legacy_feedback.description = 'Legacy feedback history and pending queue compatibility; use thread request --maintenance for new feedback.'
    feedbacks = legacy_feedback.add_subparsers(dest='feedback_command', required=True)
    for verb in ('status', 'connect', 'add', 'reply', 'list', 'show', 'pull', 'sync', 'discard'):
        fb = command(feedbacks, verb, 'feedback.' + verb)
        if verb == 'connect':
            fb.add_argument('--ssh'); fb.add_argument('--service'); fb.add_argument('--repo')
        if verb in ('show', 'reply', 'discard'):
            fb.add_argument('id')
        if verb in ('add', 'reply'):
            fb.add_argument('--title')
            body(fb)
            fb.add_argument('--product', choices=PRODUCTS, default=product)
            fb.add_argument('--component', default='')
            fb.add_argument('--sync', action='store_true')
            if verb == 'add':
                fb.add_argument('--category', choices=CATEGORIES[:-1], default='suggestion')
        if verb == 'list':
            fb.add_argument('--query'); fb.add_argument('--product', choices=PRODUCTS)
            fb.add_argument('--category', choices=CATEGORIES[:-1])
            fb.add_argument('--mine', action='store_true'); fb.add_argument('--limit', type=int, default=50)
    profiles = command(commands, 'profile').add_subparsers(dest='profile_command', required=True)
    command(profiles, 'show', 'profile.show')
    command(profiles, 'init', 'profile.init')
    personal = command(profiles, 'set', 'profile.set')
    personal.add_argument('--expect', required=True)
    for field in PROFILE_FIELDS:
        if field != 'about':
            personal.add_argument('--' + field.replace('_', '-'), default=argparse.SUPPRESS)
    personal.add_argument('--contact-stdin', action='store_true', help='Read contact text from standard input')
    personal.add_argument('--encrypt-contact', action='store_true', help='Encrypt the new contact information')
    personal.add_argument('--key-fd', type=int, help='Read the password from an inherited pipe; default: hidden terminal prompt')
    about = personal.add_mutually_exclusive_group()
    about.add_argument('--about', default=argparse.SUPPRESS)
    about.add_argument('--file', help='UTF-8 Markdown for the about field', default=argparse.SUPPRESS)
    export = command(profiles, 'export', 'profile.export')
    export.add_argument('--field', dest='fields', action='append', choices=PROFILE_FIELDS, required=True)
    for verb in ('encrypt', 'reveal', 'decrypt'):
        crypt = command(profiles, verb, 'profile.' + verb)
        crypt.add_argument('--field', choices=('contact',), required=True)
        crypt.add_argument('--key-fd', type=int, help='Read the password from an inherited pipe; default: hidden terminal prompt')
        if verb != 'reveal':
            crypt.add_argument('--expect', required=True)
    doctor = command(commands, "doctor", "doctor")
    doctor.add_argument("--agent", choices=AGENTS)
    doctor.add_argument("--dest")
    command(commands, "capabilities", "capabilities")
    clients = command(commands, 'client').add_subparsers(dest='client_command', required=True)
    command(clients, 'check', 'client.check')
    command(clients, 'update', 'client.update')
    spaces = command(commands, 'space').add_subparsers(dest='space_command', required=True)
    for verb in ('discover', 'list', 'fetch'):
        sp = command(spaces, verb, 'space.' + verb)
        sp.add_argument('--ssh', help='Use your existing SSH alias; saved after installation')
        sp.add_argument('--service')
        if verb == 'fetch':
            sp.add_argument('user', help='Server account or space ID from space list')
    command(commands, "sync", "sync")
    command(commands, 'whoami', 'whoami')
    repos = command(commands, "repo").add_subparsers(dest="verb", required=True)
    for verb in ("connect", "clone"):
        p = command(repos, verb, "repo." + verb)
        for option in ("host", "login", "repo", "identity", "known-hosts", "host-key-alias"):
            p.add_argument("--" + option, required=option == 'repo')
        p.add_argument("--port", type=int, default=22)
        p.add_argument('--service', help='Local service discovery; defaults to the shared service')
        p.add_argument('--ssh', help='Use an existing SSH host alias and its normal system login')
    hub = command(commands, "hub").add_subparsers(dest="verb", required=True)
    for verb in ("init", "repo-create", "grant", "key-add", "key-revoke", "status", "serve", "validate", 'run','community-init','maintenance'):
        p = command(hub, verb, "hub." + verb)
        p.add_argument("--hub-root", required=True)
        if verb=='community-init':p.add_argument('--library-root',required=True)
        if verb=='maintenance':
            p.add_argument('--repo');p.add_argument('--thread');p.add_argument('--clear',action='store_true')
        if verb in {"repo-create", "grant", "status", "validate"}:
            p.add_argument("--repo", required=True)
        if verb in {"repo-create", "key-add"}:
            p.add_argument("--entrypoint", required=True)
        if verb == "repo-create":
            p.add_argument("--product", choices=("cosmesh", "cosbase"), default="cosmesh")
            p.add_argument("--owner")
            p.add_argument('--system-feedback', action='store_true', help='Shared system feedback for authenticated OS accounts')
        if verb in {"grant", "key-add"}:
            p.add_argument("--user", required=True)
        if verb == "grant":
            p.add_argument("--access", choices=("read", "write", "none"), required=True)
        if verb == "key-add":
            p.add_argument("--public-key-file", required=True)
            p.add_argument("--authorize", action="store_true")
        if verb in {"key-revoke", "serve"}:
            p.add_argument("--key-id", required=True)
        if verb == 'run':
            p.add_argument('--service', required=True)
    hidden = command(commands, '_transport', '_transport')
    hidden.add_argument('--service', required=True)
    hidden.add_argument('--ssh')
    hidden.add_argument('transport_args', nargs=argparse.REMAINDER)
    remote = command(commands, '_remote', '_remote')
    remote.add_argument('--service', required=True)
    remote.add_argument('--repo', required=True)
    remote.add_argument('--operation', required=True, choices=('cosmesh-status', 'git-upload-pack', 'git-receive-pack', 'cosmesh-space'))
    self_parser = command(commands, "self", "self")
    self_parser.add_argument("management_args", nargs=argparse.REMAINDER)
    sk = command(commands, "skills").add_subparsers(dest="skill_command", required=True)
    si = command(sk, "install", "skills.install")
    si.add_argument("--agent", choices=AGENTS, required=True)
    si.add_argument("--only", choices=("cosmesh", "cosbase", "both"), default=product)
    si.add_argument("--dest", help="Skill root; defaults to ~/.agents/skills")
    se = command(sk, 'export', 'skills.export')
    se.add_argument('--ssh', required=True, help='Existing SSH alias on the user computer')
    se.add_argument('--bootstrap', default='/srv/cosmesh/agent/bootstrap.py')
    from .thread_cli import add_commands
    add_commands(commands, command, body)
    from .community_cli import add_commands as add_community_commands
    add_community_commands(commands,command)
    from .search import add_commands as add_search_commands
    add_search_commands(commands,command)
    for kind in KINDS:
        group = command(commands, kind).add_subparsers(dest="verb", required=True)
        create = command(group, "add" if kind == "update" else "create", kind + (".add" if kind == "update" else ".create"))
        create.add_argument("--title")
        create.add_argument("--project")
        create.add_argument("--source", dest="sources", action="append", default=[])
        body(create)
        if kind == "project":
            create.add_argument("--member", dest="members", action="append", default=[])
        if kind == "task":
            create.add_argument("--next", dest="next_action", default="")
            create.add_argument("--waiting", default="")
            create.add_argument("--invite", action="append", default=[])
            dates(create)
        if kind == "update":
            create.add_argument("--task")
            create.add_argument("--type", default="progress")
            create.add_argument("--reply-to")
            create.add_argument("--supersedes")
            create.add_argument("--recipient", dest="recipients", action="append", default=[])
            for field in ("agent", "host", "workdir", "session-id"):
                create.add_argument("--" + field, default=argparse.SUPPRESS)
        listing(command(group, "list", "doc.list"), kind)
        show = command(group, "show", "doc.show")
        show.add_argument("id")
        show.set_defaults(kind=kind)
        if kind == "task":
            for verb in ("finish", "reopen", "invite", "accept", "decline", "state", "schedule"):
                p = command(group, verb, "task." + verb)
                p.add_argument("id")
                if verb in ("finish", "reopen"):
                    p.add_argument("--expect")
                p.add_argument("--reason", default="")
                if verb == "finish":
                    p.add_argument("--outcome", choices=("done", "cancelled"), default="done")
                if verb in ("invite", "state"):
                    p.add_argument("--to", required=True)
                if verb in ("accept", "decline"):
                    p.add_argument("--invitation", required=True)
                if verb == "schedule":
                    dates(p)
    group = command(commands, "doc").add_subparsers(dest="verb", required=True)
    listing(command(group, "list", "doc.list"))
    for verb in ("show", "history", "edit", "convert", "derive", "archive", "trash", "restore"):
        p = command(group, verb, "doc." + verb)
        p.add_argument("id")
        if verb not in ("show", "history"):
            p.add_argument("--expect")
        if verb == "edit":
            edit_fields(p)
        if verb in ("convert", "derive"):
            p.add_argument("--to", choices=KINDS, required=True)
            p.add_argument("--title", default=argparse.SUPPRESS)
    context = command(commands, "context", "context")
    context.add_argument("id")
    command(commands, "inbox", "inbox")
    agenda = command(commands, "agenda", "agenda")
    agenda.add_argument("--from", dest="from", default=argparse.SUPPRESS)
    agenda.add_argument("--to", default=argparse.SUPPRESS)
    command(commands, "validate", "validate")
    return root


def capabilities(product):
    return {"client_version": __version__, "interface_version": 1, "schema_version": 1,
            "installation": installation(),
            "product": product, "mode": "local_unverified", "agent_targets": list(AGENTS),
            'profile': 'private',
            'document_semantics': {'project': 'context', 'task': 'action', 'update': 'record'},
            "thread_schema_version": 2, "schema_versions": [1, 2, 3], "knowledge_schema_version":3,"primary_model": "thread",
            "coordination": {"default": "personal_agents", "delegation": "ordinary_request", "requires_server_agent": False},
            "features": {"threads": True, "thread_requests": True, "thread_shared_references": True,
                         "knowledge_library":True,"knowledge_backlinks":True,"thread_archives":True,"real_name_directory":True,"contribution_history":True,
                         "thread_coordination_context": True, "thread_cited_results": True,
                         "thread_record_backlinks": True,
                         "unified_search": True, "agent_search_workflow": True, "versioned_search_sources": True,
                         "legacy_search_sources": True, "bounded_search_batch": True,
                         "thread_split_merge": True, "thread_recycle_bin": True, "thread_withdrawal": True,
                         "thread_session_lookup": True,
                         "private_session_bindings": True, "maintenance_requests": True,
                         "markdown_yaml": True, "local_git_history": True, "context": True,
                         "json_receipts": True, "idempotency": True, "optimistic_concurrency": True,
                         "personal_lifecycle": product == "cosbase", "local_control_updates": product == "cosmesh",
                         "authenticated_identity": True, "remote_sync": True, "mcp": False,
                         "hub_key_mapping": True,
                         "system_identity": True, "local_gateway": True,
                         "shared_personal_profile": True, "profile_export": True, "profile_sync": False,
                         "profile_field_encryption": True, "ssh_only_skills": True, "stdin_body": True,
                         "local_first": True, "portable_install": True,
                         "ssh_connection_reuse": True, "continuous_rpc": True, "batch_source_versions": True,
                         "public_mesh": True, "personal_mesh_space": True, "space_discovery": True,
                         'system_guide': True, 'system_feedback': True,
                         "attachments": False, "cross_system_share": False, "notifications": False,
                         "pdf_reports": False, "tui_store_integration": False}}


def profile_password(fd=None):
    """One transient secret, never accepted as an argv value, config or key file."""
    if fd is None:
        if not sys.stdin.isatty():
            raise Error('SECRET_INPUT_REQUIRED', 'Use a terminal for hidden password input, or pass an anonymous pipe with --key-fd.')
        try:
            # Refuse getpass's echoing fallback if the terminal cannot hide input.
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                value = getpass.getpass('Profile encryption password: ')
        except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
            raise Error('SECRET_INPUT_REQUIRED', 'Hidden password input was unavailable or cancelled.') from None
    else:
        try:
            mode = os.fstat(fd).st_mode
            if fd < 0 or not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                raise ValueError()
            chunks = bytearray()
            while len(chunks) <= field_crypto.MAX_SECRET:
                part = os.read(fd, 1)
                if not part or part == b'\n':
                    break
                chunks.extend(part)
            value = chunks.decode('utf-8')
        except (OSError, ValueError, UnicodeError):
            raise Error('SECRET_INPUT_INVALID', 'Use a readable pipe containing one UTF-8 password line.') from None
    if not value or len(value.encode('utf-8')) > field_crypto.MAX_SECRET or any(c in value for c in '\r\n\x00'):
        raise Error('SECRET_INPUT_INVALID', 'Provide one nonempty password line of at most 4096 UTF-8 bytes.')
    return value


def read_input(file, limit):
    if file == '-':
        raw = sys.stdin.buffer.read(limit + 1)
    else:
        path = Path(file).expanduser()
        if path.stat().st_size > limit:
            raise Error('INPUT_TOO_LARGE', 'Input exceeds the size limit.')
        with path.open('rb') as handle:
            raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise Error('INPUT_TOO_LARGE', 'Input exceeds the size limit.')
    return raw.decode('utf-8')


@networked
def execute(args, product):
    action = args.action
    if action.startswith(('library.','user.')):
        from .community_cli import execute as execute_community
        return execute_community(args)
    if action=='hub.community-init':
        return Hub(args.hub_root).initialize_community(args.library_root),None
    if action.startswith("thread."):
        from .thread_cli import execute as execute_thread
        return execute_thread(args)
    if action.startswith('search.'):
        from .search import execute as execute_search
        return execute_search(args)
    if action.startswith('space.'):
        if product != 'cosmesh':
            raise Error('PRODUCT_MISMATCH', 'Public spaces belong to CosMesh. CosBase remains private.')
        if getattr(args, 'offline', False) or getattr(args, 'dry_run', False):
            raise Error('INPUT_INVALID', 'Space discovery/fetch explicitly contacts the server; ordinary cached reads work offline.')
        from .spaces import run
        return run(args.space_command, root_for(product, getattr(args, 'root', None)),
                   args.ssh, args.service, getattr(args, 'user', None)), None
    if action.startswith('client.'):
        from . import client
        if getattr(args, 'dry_run', False) or getattr(args, 'offline', False):
            raise Error('INPUT_INVALID', 'Client check/update explicitly contacts the release server.')
        return (client.check() if action == 'client.check' else client.update()), None
    if action.startswith('guide.'):
        if getattr(args, 'require_fresh', False) or getattr(args, 'revision', None):
            raise Error('INPUT_INVALID', 'The guide belongs to the installed release; upgrade to change its version.')
        if action == 'guide.list':
            return guide.listing(), None
        if action == 'guide.show':
            return guide.show(args.topic, args.section), None
        return guide.search(args.query, args.limit), None
    if action.startswith('feedback.'):
        fb = Feedback(); verb = action.split('.')[1]
        offline, dry = getattr(args, 'offline', False), getattr(args, 'dry_run', False)
        fresh, publish = getattr(args, 'require_fresh', False), getattr(args, 'sync', False)
        if offline and (fresh or publish or verb in ('connect', 'pull', 'sync')):
            raise Error('INPUT_INVALID', 'Offline feedback cannot contact the Hub.')
        if any(getattr(args, k, None) for k in ('revision', 'expect_revision')):
            raise Error('INPUT_INVALID', 'Feedback reads use the cached accepted source; Git revision flags do not apply.')
        if fresh and verb not in ('list', 'show'):
            raise Error('INPUT_INVALID', '--require-fresh applies to feedback list or show.')
        if dry and verb not in ('add', 'reply'):
            raise Error('INPUT_INVALID', 'Feedback dry-run previews add or reply.')
        if dry and publish:
            raise Error('INPUT_INVALID', 'A feedback preview cannot publish.')
        if fresh:
            fb.pull()
        if verb == 'status':
            return fb.status(), None
        if verb == 'connect':
            return fb.connect(ssh=args.ssh, service=args.service, repo=args.repo), None
        if verb in ('pull', 'sync'):
            return getattr(fb, verb)(), None
        if verb == 'discard':
            return fb.discard(args.id), None
        if verb == 'list':
            return fb.read(args.query, args.product, args.category, args.mine, limit=args.limit), None
        if verb == 'show':
            return fb.read(doc_id=args.id), None
        text = getattr(args, 'body', '')
        if hasattr(args, 'file'):
            text = read_input(args.file, MAX_BODY)
        draft = fb.queue({'title': args.title, 'body': text, 'product': args.product,
                          'category': 'reply' if verb == 'reply' else args.category,
                          'component': args.component, 'reply_to': args.id if verb == 'reply' else None},
                         key=getattr(args, 'idempotency_key', None), dry_run=dry)
        result = {'id': draft['id'], 'saved_locally': not dry, 'publication': draft['state'],
                  'replayed': draft['replayed'], 'dry_run': dry, 'visibility_after_publish': 'hub_authenticated_users'}
        if publish:
            try:
                fb.sync()
                result['publication'] = fb.read(doc_id=draft['id'])['feedback']['publication']
                result['publication'] = next((d['state'] for _, d in fb.drafts() if d['id'] == draft['id']), result['publication'])
            except (Error, OSError, ValueError) as exc:
                result['sync_error'] = (exc if isinstance(exc, Error) else Error('IO_ERROR', str(exc))).as_dict()
        return result, None
    if action.startswith('profile.'):
        if getattr(args, 'require_fresh', False):
            raise Error('FRESHNESS_UNAVAILABLE', 'Personal profiles are local; cross-device profile sync is not configured.')
        if any(getattr(args, k, None) for k in ('revision', 'expect_revision', 'idempotency_key')):
            raise Error('INPUT_INVALID', 'Profile updates use --expect content version, independently of Git records.')
        profile = PersonalProfile()
        if action == 'profile.show':
            return profile.read(), None
        if action == 'profile.init':
            return profile.init(getattr(args, 'dry_run', False)), None
        if action == 'profile.export':
            return profile.export(args.fields), None
        if action in ('profile.encrypt', 'profile.reveal', 'profile.decrypt'):
            if action == 'profile.reveal' and getattr(args, 'dry_run', False):
                raise Error('INPUT_INVALID', 'Reveal is a read operation; use profile show to inspect ciphertext.')
            current = profile.read()
            if hasattr(args, 'expect') and args.expect != current['version']:
                raise Error('STATE_CONFLICT', 'The profile changed; read it again before revising.')
            password = profile_password(args.key_fd)
            return profile.crypt(action.split('.')[1], args.field, password,
                                 getattr(args, 'expect', None), getattr(args, 'dry_run', False)), None
        changes = {k: getattr(args, k) for k in PROFILE_FIELDS if hasattr(args, k)}
        if args.contact_stdin:
            if 'contact' in changes or args.key_fd == 0:
                raise Error('INPUT_INVALID', 'Contact stdin requires a separate password pipe and cannot be combined with --contact.')
            changes['contact'] = sys.stdin.buffer.read(MAX_PROFILE + 1).decode('utf-8')
            if len(changes['contact'].encode('utf-8')) > MAX_PROFILE:
                raise Error('INPUT_TOO_LARGE', 'Contact input exceeds 64 KiB.')
        if hasattr(args, 'file'):
            changes['about'] = read_input(args.file, MAX_PROFILE)
        current = profile.read()
        if args.expect != current['version']:
            raise Error('STATE_CONFLICT', 'The profile changed; read it again before revising.')
        needs_password = bool(changes.get('contact')) and (args.encrypt_contact or 'contact' in current['encrypted_fields'])
        if args.key_fd is not None and not needs_password:
            raise Error('INPUT_INVALID', 'A password pipe is only used when encrypting contact information.')
        password = profile_password(args.key_fd) if needs_password else None
        return profile.set(changes, args.expect, getattr(args, 'dry_run', False),
                           encrypt_contact=args.encrypt_contact, password=password), None
    if action.startswith("hub."):
        if getattr(args, "dry_run", False):
            raise Error("INPUT_INVALID", "Hub administration requires explicit execution.")
        hub = Hub(args.hub_root)
        verb = action.split(".")[1]
        if verb == "init":
            return hub.initialize(), None
        if verb == 'maintenance':
            return hub.maintenance(args.repo, args.thread, args.clear), None
        if verb == "repo-create":
            return hub.create_repo(args.repo, args.product, args.entrypoint, args.owner, args.system_feedback), None
        if verb == "grant":
            return hub.grant(args.repo, args.user, args.access), None
        if verb == "key-add":
            registration = hub.register_key(args.user, Path(args.public_key_file).read_text(), args.entrypoint)
            return (hub.authorize_key(registration) if args.authorize else registration), None
        if verb == "key-revoke":
            return hub.revoke_key(args.key_id), None
        if verb == "status":
            from .hub import actor
            return hub.status(args.repo, actor()), None
    if action == "self":
        raise Error("NOT_MANAGED_INSTALL", "This is a development or unmanaged command.",
                    "Install a release bundle and use its cosmesh self commands; the source environment remains separate.")
    root = root_for(product, getattr(args, 'root', None))
    if action == "capabilities":
        return capabilities(product), None
    if action == 'whoami':
        from .access import system_user
        return {'system': system_user(), 'document_user': Store(root).client().get('user')}, None
    if action == "skills.install":
        names = ["cosmesh", "cosbase"] if args.only == "both" else [args.only]
        return install(args.agent, names, args.dest, getattr(args, "dry_run", False)), None
    installed_prefix = os.environ.get("COSMESH_INSTALL_PREFIX")
    if installed_prefix and root.expanduser().resolve().is_relative_to(Path(installed_prefix).resolve()):
        raise Error("PATH_INVALID", "Document stores must be outside the installed application directory.",
                    "Use the default data directory or a separate --root so upgrades and uninstall preserve documents.")
    if action == "doctor":
        result = {"capabilities": capabilities(product), "python": sys.version.split()[0],
                  "python_executable": sys.executable, "client_entry": str(Path(sys.argv[0]).absolute()),
                  "git": shutil.which("git"), "root": str(root), "store": "not_initialized"}
        revision = None
        if (root / ".cosmesh/config.json").exists():
            result["validation"], revision = Service(Store(root), product).run("validate")
            result["store"] = "local_unverified"
        if args.agent:
            result["agent"] = inspect(args.agent, [product], args.dest)
        return result, revision
    store = Store(root)
    if getattr(args, "offline", False) and (action in {"sync", "repo.connect", "repo.clone"} or getattr(args, "require_fresh", False)):
        raise Error("INPUT_INVALID", "Offline mode cannot contact the Hub or require a fresh remote.")
    if getattr(args, "dry_run", False) and action in {"sync", "repo.connect", "repo.clone"}:
        raise Error("INPUT_INVALID", "Transport operations require explicit execution; dry-run is for document changes.")
    if action in {"repo.connect", "repo.clone"}:
        from .access import DEFAULT_DISCOVERY
        if args.ssh:
            config = {'kind': 'ssh-system', 'host': args.ssh, 'repo': args.repo, 'discovery': args.service or DEFAULT_DISCOVERY}
        elif args.host or args.identity:
            config = {key: getattr(args, key) for key in ("host", "port", "login", "repo", "identity", "known_hosts", "host_key_alias")}
            if any(config[key] is None for key in config):
                raise Error('INPUT_INVALID', 'Legacy SSH mode needs all transport options; local system access only needs --repo.')
        else:
            config = {'kind': 'system', 'discovery': args.service or os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY), 'repo': args.repo}
        return configure(store, product, config) if action == "repo.connect" else clone(root, product, config)
    if action == "sync":
        return synchronize(store, product)
    if getattr(args, "require_fresh", False):
        if action not in READS:
            raise Error("INPUT_INVALID", "--require-fresh applies to reads; synchronize explicitly before writing.")
        synchronize(store, product)
    if action == "init":
        from .access import system_user
        return initialize(store, product, args.user or system_user()['user'],
                          args.timezone if args.timezone is not None else PersonalProfile().default_timezone(),
                          getattr(args, "dry_run", False))
    excluded = {"root", "json", "command", "verb", "action", "skill_command", "revision",
                "expect_revision", "idempotency_key", "dry_run", "offline", "require_fresh"}
    payload = {key: value for key, value in vars(args).items() if key not in excluded and value is not None}
    # Explicit date clears carry None; omitted fields must remain absent.
    for field in ("due", "followup"):
        if hasattr(args, field):
            payload[field] = getattr(args, field)
    if "file" in payload:
        payload['body'] = read_input(payload.pop('file'), MAX_DOCUMENT)
    session_keys = ("agent", "host", "workdir", "session_id")
    if any(key in payload for key in session_keys):
        if not all(payload.get(key) for key in session_keys[:3]):
            raise Error("INPUT_INVALID", "Session registration requires actual --agent, --host and --workdir.")
        payload["session"] = {("id" if key == "session_id" else key): payload.pop(key)
                              for key in session_keys if key in payload}
    return Service(store, product).run(action, payload, getattr(args, "revision", None),
                                       getattr(args, "expect_revision", None),
                                       getattr(args, "idempotency_key", None), getattr(args, "dry_run", False))


def main(argv=None, product="cosmesh"):
    argv = sys.argv[1:] if argv is None else argv
    machine = "--json" in argv
    revision, data, error = None, None, None
    try:
        args = parser(product).parse_args(argv)
        if args.action == 'skills.export':
            if machine or getattr(args, 'dry_run', False):
                raise Error('INPUT_INVALID', 'Skill export emits an installer script; omit --json and --dry-run.')
            from .remote_skills import export
            print(export(args.ssh, args.bootstrap), end='')
            return 0
        if args.action in {'hub.run', '_transport', '_remote'}:
            try:
                if getattr(args, 'dry_run', False):
                    raise Error('INPUT_INVALID', 'A transport service cannot run in document dry-run mode.')
                from .gateway import run, git_client, status, open_request, pump
                if args.action == 'hub.run':
                    run(args.hub_root, args.service)
                    return 0
                if args.action == '_transport':
                    arguments = args.transport_args
                    if arguments[:1] == ['--']:
                        arguments = arguments[1:]
                    if args.ssh:
                        import shlex, subprocess
                        if len(arguments) != 2 or arguments[0] != 'cosmesh':
                            raise Error('INPUT_INVALID', 'Malformed Git gateway invocation.')
                        parts = shlex.split(arguments[1])
                        if len(parts) != 2 or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}\.git', parts[1]) or parts[0] not in {'git-upload-pack', 'git-receive-pack'}:
                            raise Error('COMMAND_DENIED', 'Invalid Git service operation.')
                        from .sync import system_ssh, system_command
                        config = {'host': args.ssh, 'discovery': args.service, 'repo': parts[1][:-4]}
                        return subprocess.call(system_ssh(args.ssh) + [system_command(config, parts[0])])
                    return git_client(args.service, arguments)
                config = {'kind': 'system', 'repo': args.repo, 'discovery': args.service}
                if args.operation == 'cosmesh-space':
                    from .gateway import space_control, receive
                    print(json.dumps(space_control(config, receive(sys.stdin.buffer))))
                    return 0
                if args.operation == 'cosmesh-status':
                    print(json.dumps(status(config)))
                    return 0
                connection, _ = open_request(config, args.operation)
                try:
                    pump(connection, sys.stdin.buffer, sys.stdout.buffer)
                finally:
                    connection.close()
                return 0
            except (Error, OSError, ValueError) as exc:
                print('[COSMESH:%s] %s' % (getattr(exc, 'code', 'SYNC_NETWORK'), str(exc)), file=sys.stderr)
                return 1
        if args.action in {"hub.serve", "hub.validate"}:
            try:
                hub = Hub(args.hub_root)
                if args.action == "hub.serve":
                    return hub.serve(args.key_id)
                key = os.environ.get("COSMESH_RECEIVE_KEY", "")
                system_uid = os.environ.get('COSMESH_RECEIVE_SYSTEM_UID')
                if system_uid is not None:
                    from .access import system_user
                    hub.settings()
                    user = system_user(int(system_uid))['user']
                else:
                    user = hub.identity(key)
                if (os.environ.get("COSMESH_HUB_ROOT") != str(hub.root) or
                        os.environ.get("COSMESH_RECEIVE_REPO") != args.repo or
                        os.environ.get("COSMESH_RECEIVE_ACTOR") != user or
                        not os.environ.get("COSMESH_RECEIVE_REQUEST")):
                    raise Error("AUTH_DENIED", "Receive validation requires the authenticated Hub wrapper.")
                updates = [line.split() for line in sys.stdin.read(8193).splitlines()]
                if any(len(item) != 3 or any(not re.fullmatch(r'[0-9a-f]{40}', x) for x in item[:2]) for item in updates):
                    raise Error("INPUT_INVALID", "Malformed receive request.")
                hub.validate_push(args.repo, user, updates)
                return 0
            except (Error, OSError, ValueError, KeyError) as exc:
                print("[COSMESH:%s] %s" % (getattr(exc, "code", "HUB_VALIDATION_FAILED"), str(exc)), file=sys.stderr)
                return 1
        data, revision = execute(args, product)
    except Error as exc:
        error = exc.as_dict()
    except (OSError, UnicodeError, ValueError) as exc:
        error = Error("IO_ERROR", str(exc)).as_dict()
    except Exception:
        error = Error("INTERNAL_ERROR", "Unexpected client error; preserve local files and any pending journal for diagnosis.").as_dict()
    sync_state = {"state": "local_only", "source_revision": revision, "remote_revision": None,
                  "last_sync_at": None, "identity_verified": False}
    try:
        if 'args' in locals() and not args.action.startswith(('hub.', 'profile.', 'guide.', 'feedback.', 'client.', 'space.', 'thread.', 'library.','user.','search.')):
            root = root_for(product, getattr(args, "root", None))
            sync_state = state(Store(root), revision)
    except (Error, OSError, ValueError):
        pass
    profile_action = 'args' in locals() and args.action.startswith('profile.')
    guide_action = 'args' in locals() and args.action.startswith('guide.')
    if 'args' in locals() and hasattr(args, '_thread_sync_state'):
        sync_state = args._thread_sync_state
    elif 'args' in locals() and args.action.startswith('thread.'):
        try:
            from .threads import Workbench, workspace_root
            workbench = Workbench(workspace_root(getattr(args, 'root', None)))
            with workbench.base.locked():
                workbench.load()
                sync_state = workbench.status()
        except (Error, OSError, ValueError):
            pass
    client_action = 'args' in locals() and args.action.startswith(('client.', 'space.', 'library.','user.','search.'))
    if 'args' in locals() and args.action.startswith('feedback.'):
        try:
            status = Feedback().status()
            sync_state = status['sync'] or sync_state
            if status['pending_count']:
                sync_state.update(state='needs_sync', identity_verified=False)
            if isinstance(data, dict) and data.get('sync_error'):
                sync_state.update(state='offline' if data['sync_error']['code'] == 'SYNC_NETWORK' else 'sync_failed')
        except (Error, OSError, ValueError):
            pass
    result = {"ok": error is None, "data": data,
              "sync": None if profile_action or guide_action or client_action else sync_state,
              "warnings": [] if profile_action or guide_action or client_action or sync_state["identity_verified"] else ["Local source has not been verified as accepted by the configured Hub."],
              "error": error}
    print(json.dumps(result, ensure_ascii=False, indent=None if machine else 2))
    if error:
        if error["code"] in {"STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", "STORE_BUSY", "STORE_DIRTY", 'PROFILE_BUSY', 'FEEDBACK_BUSY'}:
            return 4
        if error["code"] in {"AUTH_DENIED", "POLICY_DENIED", "NEEDS_PROJECT_ACCESS", 'PROFILE_PERMISSIONS'}:
            return 3
        return 2
    return 0


def base_main():
    return main(product="cosbase")
