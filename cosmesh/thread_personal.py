"""Owner-only synchronization of one existing Thread, with additive local recovery."""
import json

from .errors import Error
from .personal import Personal
from .thread_locations import ThreadLocations
from .personal_protocol import MAX_METADATA, digest, merge_maps, packed, private_dir, read_json, save_json
from .protocol import decode
from .store import atomic
from .thread_protocol import Snapshot, content_version, identifier, references
from .thread_personal_protocol import FORMAT, MAPS, belongs, empty, protect_history, validate_manifest


class ThreadPersonal(ThreadLocations, Personal):
    def __init__(self, wb):
        super().__init__(wb)
        self.directory = private_dir(self.root / 'threads')
        self.index = read_json(self.directory / 'index.json', {})
        pending = read_json(self.directory / 'apply.json')
        if pending:
            self.apply_thread(pending)

    def capability(self):
        info = self.rpc({'method': 'personal_info'})
        if info.get('thread_protocol') != FORMAT:
            raise Error('FEATURE_UNAVAILABLE', 'This Hub needs selected-Thread personal sync. No whole-store fallback is allowed.')
        self.server_info = info
        return info

    def view(self, tid):
        return read_json(self.directory / (identifier(tid, 'W') + '.json'), empty(tid))

    def manifest(self, tid):
        """Select before resolving history. Never follow references into other Threads."""
        tid = identifier(tid, 'W')
        files = self.wb._models['local'][1]
        value = self.view(tid)
        docs = self.wb._models['local'][2].docs
        selected = {k: d for k, d in docs.items() if belongs(d, tid)}
        if tid not in selected:
            return empty(tid)
        previous = self.index.get(tid, {}).get('local_revision')
        history_range = []
        if previous and self.wb.base.git('merge-base','--is-ancestor',previous,self.wb.base.head,check=False).returncode == 0:
            history_range = [previous + '..HEAD']
        value['documents'] = {'documents/' + k + '.md': files['documents/' + k + '.md'] for k in selected}
        for path, text in value['documents'].items():
            doc = decode(text)
            value['history'][doc['meta']['id'] + '@' + content_version(doc)] = text
            # Only paths selected above are traversed. All their prior authored versions survive.
            for commit in self.wb.base.git('log', '--format=%H', *history_range, '--', path).splitlines():
                old = self.wb.base.git('show', commit + ':' + path, check=False)
                if not old.returncode:
                    doc = decode(old.stdout)
                    if belongs(doc, tid):
                        value['history'][doc['meta']['id'] + '@' + content_version(doc)] = old.stdout
        for key, text in read_json(self.root / 'sources.json', {}).items():
            if belongs(decode(text), tid): value['history'][key] = text
        value['operations'] = {path: text for path, text in files.items()
                               if path.startswith('.cosmesh/operations/') and
                               json.loads(text).get('result', {}).get('id') in selected}
        value['bindings'] = self.binding_rows(tid, value['bindings'])
        value['assets'] = self.selected_assets(tid)
        validate_manifest(value, self.wb.base)
        return value

    def selected_assets(self, tid):
        paths = self.state.get('asset_paths', {})
        return {k: dict(v, **({'relative_path': paths[k]} if k in paths else {}))
                for k, v in self.state['assets'].items() if v['thread'] == tid}

    def load_manifest(self, sha, tid):
        value = json.loads(self.download(sha, metadata=True).read_bytes())
        validate_manifest(value, self.wb.base)
        if value['thread'] != tid: raise Error('POLICY_DENIED', 'Relay returned another Thread.')
        return value

    def local_changes(self, value):
        _, current = self.wb.base.snapshot()
        candidate = {**current, **value['documents'], **value['operations']}
        # Also validate against unrelated destination Threads, retaining their configuration.
        Snapshot(self.wb.base, candidate)
        return current, {k: v for k, v in candidate.items() if current.get(k) != v}

    def apply_thread(self, pending):
        value = pending['snapshot']; tid = value['thread']
        validate_manifest(value, self.wb.base)
        current, changes = self.local_changes(value)
        if self.wb.base.head != pending['before'] and changes:
            raise Error('RECOVERY_REQUIRED', 'Interrupted Thread restore and local work both changed; the journal and both versions are retained.')
        if changes: self.wb.base.commit(changes, 'thread-personal-sync')
        # Reuse the existing fixed-source fallback so older clients can read restored history.
        sources = read_json(self.root / 'sources.json', {})
        for key, text in value['history'].items():
            if key in sources and sources[key] != text:
                raise Error('CHECKSUM_MISMATCH', 'A retained historical source differs.')
            sources[key] = text
        save_json(self.root / 'sources.json', sources)
        for aid, row in value['assets'].items():
            self.state['assets'][aid] = {k: v for k, v in row.items() if k != 'relative_path'}
            if 'relative_path' in row: self.state.setdefault('asset_paths', {})[aid] = row['relative_path']
        self.save()
        save_json(self.directory / (tid + '.json'), value)
        self.wb.client.setdefault('personal_thread_authors', {})[tid] = decode(value['documents']['documents/' + tid + '.md'])['meta']['author']
        self.wb.save_client()
        self.index[tid] = {'base': pending['base'], 'mode': 'personal', 'local_revision': self.wb.base.head}
        save_json(self.directory / 'index.json', self.index)
        (self.directory / 'apply.json').unlink(missing_ok=True)
        self.wb.load()
        from .status_pages import schedule
        schedule(self.wb)

    def synchronize(self, tid, pull=False, files=False):
        identifier(tid, 'W')
        if pull and files: raise Error('INPUT_INVALID', '--pull never uploads files.')
        self.capability()  # Must precede any blob upload, including files.
        local = self.manifest(tid)
        head = self.rpc({'method': 'personal_thread_head', 'thread': tid})['sha']
        if not head and (pull or not local['documents']):
            raise Error('NOT_FOUND', 'This Thread is unavailable in your private relay.')
        remote = self.load_manifest(head, tid) if head else None
        previous = self.index.get(tid, {}).get('base')
        intent_path = self.directory / (tid + '.intent.json')
        intent = read_json(intent_path)
        if intent and head == intent['sha']:
            # The accepted candidate may include remote additions not applied locally
            # before a lost reply. Preserve them while folding in subsequent local work.
            local = dict(format=FORMAT, thread=tid, **{k: merge_maps(intent['local'][k], local[k], remote[k]) for k in MAPS})
            previous = head
        if previous and not head: raise Error('HISTORY_PROTECTED', 'The previously accepted Thread is missing from the relay.')
        base = self.load_manifest(previous, tid) if previous else empty(tid)
        merged = dict(format=FORMAT, thread=tid, **{k: merge_maps(base[k], local[k], remote[k])
                      if remote else local[k] for k in MAPS})
        validate_manifest(merged, self.wb.base)
        if remote: protect_history(remote, merged)
        self.local_changes(merged)  # Conflict detection before uploading or changing local files.
        transfers = None
        if files:
            ids = [k for k, row in self.state['assets'].items() if row['thread'] == tid]
            advanced = [k for k in ids if merged['assets'][k]['blob'] != self.state['assets'][k]['blob']]
            transfers = self.push([k for k in ids if k not in advanced])
            transfers['files'].extend(dict(id=k,name=self.state['assets'][k]['name'],state='skipped',
                reason='LOCAL_FILE_DIVERGED',bytes=self.state['assets'][k]['size']) for k in advanced)
            for entry in transfers['files']:
                if entry['state']=='server_available':
                    aid=entry['id']; merged['assets'][aid].update(self.state['assets'][aid])
        raw = packed(merged).encode()
        if len(raw) > MAX_METADATA: raise Error('INPUT_TOO_LARGE', 'Thread manifest exceeds its limit; local sources are retained.')
        accepted = head
        if not pull:
            sha = digest(raw); path = self.cache / sha
            if not path.exists():
                self.cache_room(len(raw)); atomic(path, raw.decode()); path.chmod(0o600)
            self.upload(path, sha, len(raw))
            save_json(intent_path, {'sha': sha, 'expect': head, 'local': local})
            receipt = self.rpc({'method': 'personal_thread_commit', 'thread': tid, 'sha': sha, 'expect': head})
            if receipt.get('accepted') is not True or receipt.get('sha') != sha:
                raise Error('SYNC_PROTOCOL', 'Thread acceptance was not confirmed; retry the same Thread.')
            accepted = sha
        pending = {'before': self.wb.base.head, 'snapshot': merged, 'base': accepted}
        save_json(self.directory / 'apply.json', pending); self.apply_thread(pending)
        intent_path.unlink(missing_ok=True)
        missing = []
        for text in merged['documents'].values():
            for ref in references(decode(text)['meta']):
                if ref['repo'] == 'local' and ref['id'] + '@' + ref['version'] not in merged['history'] and ref not in missing:
                    missing.append(ref)
        return dict(thread=tid, sharing='personal', audience='owner', published=False,
                    accepted_snapshot=accepted, uploaded=not pull, pulled=bool(head),
                    restored_records=len(merged['documents']), missing_private_references=missing,
                    assets_fetched=0, assets=self.asset_status(merged), transfers=transfers)

    def asset_status(self, value):
        from .thread_files import availability
        return [{'id': aid, 'name': row['name'], 'bytes': row['size'],
                 'state': 'server_available' if row['blob'] else 'source_only',
                 'device': row['device'], 'path': row['path'], 'relative_path': row.get('relative_path', row['name']),
                 **availability(self, aid, row)} for aid, row in value['assets'].items()]
