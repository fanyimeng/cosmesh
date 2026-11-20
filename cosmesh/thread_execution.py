"""Cross-device private locations for a public Thread; public records stay in Mesh."""
import json

import socket

from .errors import Error
from .personal import Personal
from .personal_protocol import MAX_METADATA, digest, merge_maps, packed, private_dir, read_json, save_json
from .thread_execution_protocol import FORMAT, protect, validate
from .thread_locations import ThreadLocations
from .thread_protocol import identifier
from .store import atomic


class ThreadExecution(ThreadLocations, Personal):
    supports_files = False

    def __init__(self, wb):
        Personal.__init__(self, wb)
        self.directory = private_dir(self.root / 'executions')

    def capability(self):
        info = self.rpc({'method': 'personal_info'})
        if info.get('execution_protocol') != FORMAT:
            raise Error('FEATURE_UNAVAILABLE', 'This Hub needs private execution sync for public Threads; public content is not copied into personal sync.')
        return info

    def view(self, tid):
        return read_json(self.directory / (identifier(tid, 'W') + '.json'), {'bindings': {}})

    def selected_assets(self, tid):
        return {}  # Public attachments retain their existing fixed-reference flow.

    def locations(self, tid):
        result = super().locations(tid)
        result.update(sharing='public', personal_sync='execution' if 'source' in self.view(result['thread']) else 'off')
        return result

    def load_manifest(self, sha, tid):
        value = json.loads(self.download(sha, metadata=True).read_bytes())
        validate(value)
        if value['thread'] != tid:
            raise Error('POLICY_DENIED', 'Relay returned another Thread.')
        return value

    def check_connection(self):
        client = self.wb.client
        if ((client.get('actor') and client['actor'] != self.state['actor']) or
                (client.get('connection') and client['connection'] != self.state['connection'])):
            raise Error('AUTH_DENIED', 'Public content and private locations must use the same Hub identity.')

    def public_source(self, tid, remote):
        """Fetch only the referenced public repository, never push local public drafts."""
        self.check_connection()
        known = self.wb.catalog.get(tid)
        if known and known['repo'] != 'local':
            source = {'repo': known['repo'], 'owner': self.wb.client['spaces'][known['repo']]['owner']}
            if remote and remote['source'] != source:
                raise Error('REFERENCE_INVALID', 'Private execution metadata differs from the public Thread source.')
        elif remote:
            source = remote['source']
            if known and (source['owner'] != self.state['actor'] or
                          known['doc']['meta']['author'] not in self.wb.owned_authors(known)):
                raise Error('ID_CONFLICT', 'A local private Thread already uses this public ID; preserve it and resolve the conflict first.')
        else:
            raise Error('NOT_FOUND', 'Discover the public Thread before enabling its private execution sync.')
        # cache() verifies the authenticated account, source and existing connection.
        self.wb.user = self.state['actor']
        self.wb.cache(self.state['connection'], {'id': source['repo'], 'owner': source['owner']})
        if source['owner'] == self.state['actor']:
            if self.wb.client.get('own_repo') not in (None, source['repo']):
                raise Error('REFERENCE_INVALID', 'Public source differs from the saved owner repository.')
            self.wb.client['own_repo'] = source['repo']
        self.wb.client.update(actor=self.state['actor'], connection=self.state['connection'])
        self.wb.save_client(); self.wb.load()
        item = self.wb.find(tid, 'thread')
        if item['repo'] != source['repo']:
            raise Error('REFERENCE_INVALID', 'The referenced public repository does not contain this Thread.')
        return source

    def synchronize(self, tid, pull=False, files=False):
        tid = identifier(tid, 'W')
        if files:
            raise Error('INPUT_INVALID', 'Public Thread personal sync carries execution locations only; open public files through their fixed library references.')
        self.check_connection(); self.capability()
        head = self.rpc({'method': 'personal_execution_head', 'thread': tid})['sha']
        if pull and not head:
            raise Error('NOT_FOUND', 'No private execution information is stored for this Thread and account.')
        remote = self.load_manifest(head, tid) if head else None
        source = self.public_source(tid, remote)
        local = self.view(tid)
        if 'source' in local and local['source'] != source:
            raise Error('REFERENCE_INVALID', 'Retained execution metadata points to another public source.')
        # Retain this owner's locations when a personally synced Thread becomes public.
        former = read_json(self.root / 'threads' / (tid + '.json'), {'bindings': {}})
        bindings = self.binding_rows(tid, merge_maps({}, former['bindings'], local['bindings']))
        if remote: bindings = merge_maps({}, bindings, remote['bindings'])
        value = dict(format=FORMAT, thread=tid, source=source, bindings=bindings)
        validate(value)
        if remote: protect(remote, value)
        raw = packed(value).encode()
        if len(raw) > MAX_METADATA:
            raise Error('INPUT_TOO_LARGE', 'Private execution metadata exceeds its limit; local locations are retained.')
        accepted = head
        if not pull:
            sha = digest(raw); path = self.cache / sha
            if not path.exists():
                self.cache_room(len(raw)); atomic(path, raw.decode()); path.chmod(0o600)
            self.upload(path, sha, len(raw))
            receipt = self.rpc({'method': 'personal_execution_commit', 'thread': tid, 'sha': sha, 'expect': head})
            if receipt.get('accepted') is not True or receipt.get('sha') != sha:
                raise Error('SYNC_PROTOCOL', 'Private execution acceptance was not confirmed; retry this Thread.')
            accepted = sha
        # One atomic sidecar write. No document/history/author mapping is applied.
        save_json(self.directory / (tid + '.json'), value)
        from .thread_execution_projection import owner_projection
        metadata = owner_projection(value['bindings'], current_device=self.state['device'], current_host=socket.gethostname())
        return dict(thread=tid, sharing='public', audience='owner', personal_sync='execution',
                    published=False, public_content='fetched', source=source,
                    accepted_snapshot=accepted, uploaded=not pull, pulled=bool(head),
                    execution_metadata=metadata,
                    restored_records=0, assets_fetched=0, assets=[], transfers=None,
                    locations_synced=len(bindings))

    def restore(self, tid, *args, **kwargs):
        result = super().restore(tid, *args, **kwargs)
        result['restoration']['records'] = 'public_source_fetched'
        return result

    def asset_status(self, value):
        return []
