"""Command-scoped, authenticated access to the configured Hub."""
from .errors import Error
from .protocol import USER
from .spaces import connection


def rpc(config, payload):
    from .spaces import rpc as request
    return request(config, payload)


class Online:
    def __init__(self, wb):
        self.wb = wb
        expected_actor = wb.client.get('actor')
        self.config = wb.client.get('connection')
        if not self.config:
            from .personal_protocol import read_json
            state=read_json(wb.base.path('.cosmesh/personal-v1/state.json'), {})
            self.config=state.get('connection') or connection(wb.base.root)
            expected_actor = expected_actor or state.get('actor')
        try:self.info=rpc(self.config, {'method':'thread_access_info'})
        except Error as exc:
            if exc.code in {'INPUT_INVALID','COMMAND_DENIED'}:
                raise Error('FEATURE_UNAVAILABLE','This Hub needs Thread access and online viewing support.')
            raise
        if self.info.get('protocol')!='thread.access.v1': raise Error('FEATURE_UNAVAILABLE','This Hub needs Thread access and online viewing support.')
        actor = self.info.get('actor')
        if not isinstance(actor, str) or not USER.fullmatch(actor) or (self.info.get('person') or {}).get('account') != actor:
            raise Error('SYNC_PROTOCOL', 'The Hub did not return a verified account.')
        if wb.client.get('hub_id') and wb.client['hub_id'] != self.info.get('hub_id'):
            raise Error('STATE_CONFLICT', 'The configured Hub identity changed.')
        if expected_actor and expected_actor != actor:
            raise Error('AUTH_DENIED','Hub identity differs from this local client.')

    def call(self, method, **payload):
        reply=rpc(self.config, {'method':method, **payload})
        if reply.get('actor') != self.info.get('actor'):
            raise Error('AUTH_DENIED','Hub identity changed during this operation.')
        return reply
