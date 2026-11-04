"""An Agent-authored project increment, with exact deduplication and scoped publication."""
import hashlib
import json
import os
from pathlib import Path

from .errors import Error
from .store import atomic
from .threads import now


def private_root(wb):
    directory = '.cosmesh/project-updates/'
    exclude = wb.base.path('.git/info/exclude')
    if directory not in exclude.read_text().splitlines():
        atomic(exclude, exclude.read_text() + '\n' + directory + '\n')
    path = wb.base.path(directory)
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise Error('AUTH_DENIED', 'Project update checkpoints must be private.')
    return path


def signature(wb, tid):
    refs = [wb.ref(wb.find(tid)), *[wb.ref(r) for r in wb.records(tid)]]
    return hashlib.sha256(json.dumps(sorted(refs, key=lambda r: r['id']), sort_keys=True).encode()).hexdigest()


def check_pending(wb, repo, allowed):
    store = wb.stores[repo]
    accepted = store.client().get('remote_revision')
    if not accepted:
        raise Error('SOURCE_PENDING', 'The target shared space has no accepted baseline.')
    if accepted != store.head:
        paths = store.git('diff', '--name-only', accepted, store.head).splitlines()
        for path in paths:
            if path.startswith('documents/') and Path(path).stem in allowed:
                continue
            if path.startswith('.cosmesh/operations/'):
                receipt = json.loads(store.git('show', store.head + ':' + path))
                if receipt.get('result', {}).get('id') in allowed:
                    continue
            raise Error('UNRELATED_PENDING', 'Other unpublished changes are retained; only this project update may be published.')


def update(wb, args, body, key=None, dry=False):
    item = wb.find(args.id, 'thread'); tid = item['doc']['meta']['id']; repo = item['repo']
    if not body.strip():
        raise Error('INPUT_INVALID', 'Provide the Agent-authored increment; an empty update creates no record.')
    if getattr(args, 'basis', None) and args.basis != signature(wb, tid):
        raise Error('STATE_CONFLICT', 'This Thread changed; compare the new records before retrying your increment.')
    if repo != 'local':
        from .thread_materials import PRIVATE_PATH
        body = PRIVATE_PATH.sub('〔私人定位省略〕', body)
        if getattr(wb, 'generation', None):
            wb.generation = {k: PRIVATE_PATH.sub('〔私人定位省略〕', v) for k, v in wb.generation.items()}
    kind = args.kind
    refs = [wb.citation(r) for r in args.ref]
    author = wb.config['user'] if repo == 'local' else wb.user
    digest = hashlib.sha256(json.dumps([tid, author, kind, body.strip(), refs], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # Author/content comparison also catches a fresh invocation with a different operation key.
    existing = next((r for r in reversed(wb.records(tid)) if r['doc']['meta']['type'] == kind and
                     r['doc']['meta']['author'] == author and r['doc']['body'].strip() == body.strip() and
                     r['doc']['meta'].get('refs', []) == refs), None)
    if existing:
        record = wb.present(existing); disposition = 'unchanged'
    else:
        record = wb.append(tid, body, kind, refs=refs,
                           key=key or 'project-update-' + digest, dry_run=dry)
        disposition = 'replayed' if record.get('replayed') else 'created'
        if not dry:
            wb.load()
    publication = {'state': 'private' if repo == 'local' else 'local_only', 'accepted': False}
    if repo != 'local' and not args.local_only and not dry:
        if getattr(args, 'offline', False):
            publication = {'state': 'pending', 'accepted': False, 'error': {'code': 'OFFLINE', 'message': 'Saved locally; retry this update online.'}}
        else:
            try:
                check_pending(wb, repo, {record['id']})
                result = wb.synchronize(knowledge_keys=[], publish_repos=[repo])
                accepted = wb.accepted(wb.find(record['id']))
                publication = {'state': 'accepted' if accepted else 'pending', 'accepted': accepted, 'errors': result['errors']}
            except Error as error:
                publication = {'state': 'pending', 'accepted': False, 'error': error.as_dict()}
    if not dry:
        atomic(private_root(wb) / (tid + '.json'), json.dumps({'thread': tid, 'record': record['id'],
               'source': wb.ref(wb.find(record['id'])), 'input_digest': digest, 'operation_key': key or 'project-update-' + digest,
               'publication': publication, 'at': now()}, ensure_ascii=False))
    from .thread_chat import summary
    return {'format': 'thread.update.v1', 'thread': tid, 'disposition': disposition,
            'record': {k: v for k, v in record.items() if k not in {'body', 'generation'}},
            'summary': summary(body), 'saved_locally': not dry, 'dry_run': dry, 'publication': publication,
            'basis': signature(wb, tid), 'notifications': 'not_requested',
            'read_hint': '直接报告新增／已有记录和本地／Mesh状态；同步失败复用同一输入重试，不重复记录，不上传附件或通知他人。'}
