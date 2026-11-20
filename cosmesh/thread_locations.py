"""Shared private execution bindings and explicit destination registration."""
import socket

from .errors import Error
from .store import safe_path


class ThreadLocations:
    supports_files = True

    def binding_rows(self, tid, known):
        from .thread_execution_reader import binding_rows
        return binding_rows(self.wb,tid,self.state,known)

    def locations(self, tid):
        tid = self.wb.find(tid, 'thread')['doc']['meta']['id']
        value = self.view(tid)
        # A location read never builds or hashes a document/history snapshot.
        value['bindings'] = self.binding_rows(tid, value['bindings'])
        value['assets'] = self.selected_assets(tid)
        from .thread_execution_projection import owner_projection
        metadata = owner_projection(value['bindings'], current_device=self.state['device'], current_host=socket.gethostname())
        return {'thread': tid, 'private': True, 'locations': [dict(id=k, **row,
                current_device=row['device'] == self.state['device'] and row['binding']['host']==socket.gethostname()) for k, row in sorted(value['bindings'].items())],
                'execution_metadata': metadata,
                'assets': self.asset_status(value)}

    def restore(self, tid, workdir=None, agent=None, project_id=None, project_name=None, session_id=None, files=False):
        if files and not self.supports_files:
            raise Error('INPUT_INVALID', 'Public Thread personal sync carries execution locations only; open public files through their fixed library references.')
        if files and not workdir: raise Error('INPUT_INVALID', '--files needs an explicit destination --workdir and --agent.')
        if workdir:
            path = safe_path(workdir)
            if path.exists() and not path.is_dir(): raise Error('PATH_INVALID', 'The destination must be a directory.')
            if not agent or not agent.strip(): raise Error('INPUT_INVALID', 'Specify the actual destination Agent.')
            if project_id and not project_name: raise Error('INPUT_INVALID', 'Provide the actual project name with its native ID.')
        elif any((agent, project_id, project_name, session_id)):
            raise Error('INPUT_INVALID', 'Destination metadata requires --workdir.')
        result = self.synchronize(tid, pull=True)
        if workdir:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.wb.bind(tid, agent, str(path), session_id)
            from .agent_projects import bind
            bind(self.wb, tid, agent, project_name or path.name, project_id,
                 kind='project' if project_name or project_id else 'directory', workdir=str(path))
            self.wb.load()
            # Registration is an explicit part of restoring here; send this Thread only.
            result = self.synchronize(tid)
            result['destination'] = str(path)
        result.update(restored=True, new_thread_created=False, project_files_overwritten=False)
        if files:
            from .thread_files import materialize
            result['files'] = materialize(self, self.view(tid), path)
        else:
            result['files'] = {'requested': False, 'state': 'not_requested', 'files': []}
        result['restoration'] = {'records': 'restored', 'files': result['files']['state'],
                                 'execution': 'registered_only' if workdir else 'not_registered'}
        result['assets'] = self.asset_status(self.view(tid))
        value = self.view(tid)
        value['bindings'] = self.binding_rows(tid, value['bindings'])
        from .thread_execution_projection import owner_projection
        result['execution_metadata'] = owner_projection(value['bindings'], current_device=self.state['device'], current_host=socket.gethostname())
        return result
