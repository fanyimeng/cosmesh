"""Windows DACL policy and native handle tests using disposable private fixtures."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cosmesh._platform import private_dacl
from cosmesh.errors import Error


class WindowsPrivatePolicyTests(unittest.TestCase):
    def test_windows_local_name_is_independent_of_document_author(self):
        from cosmesh import _platform
        from cosmesh.access import system_user
        with patch.object(_platform, 'WINDOWS', True), patch.object(_platform, 'account_name', return_value='本机 用户'):
            self.assertEqual(system_user(), {'uid': None, 'user': '本机 用户'})

    def test_replace_retries_only_transient_windows_errors_with_a_bound(self):
        from cosmesh import _platform
        def error(code):
            exc = OSError('simulated Windows result')
            exc.winerror = code
            return exc
        for outcomes, ticks, failure, calls in [
                ([error(32), None], [0, 0], False, 2),
                ([error(5), error(5)], [0, 0, 3], True, 2),
                ([error(2)], [0], True, 1)]:
            with self.subTest(outcomes=outcomes), patch.object(_platform, 'WINDOWS', True), \
                    patch.object(os, 'replace', side_effect=outcomes) as replace, \
                    patch.object(time, 'monotonic', side_effect=ticks), patch.object(time, 'sleep'):
                if failure:
                    with self.assertRaises(OSError) as raised:
                        _platform.replace_managed_file('source', 'destination')
                    self.assertIs(raised.exception, outcomes[-1])
                else:
                    _platform.replace_managed_file('source', 'destination')
                self.assertEqual(replace.call_count, calls)

    def test_only_trusted_grants_and_owner(self):
        user = 'S-1-5-21-1-2-3-1001'
        admins = 'S-1-5-32-544'
        system = 'S-1-5-18'
        everyone = 'S-1-1-0'
        safe = [(0, 3, user), (0, 3, admins), (0, 3, system),
                (0, 0x08, 'S-1-3-0'), (1, 0, everyone)]
        self.assertTrue(private_dacl(user, user, safe))
        self.assertFalse(private_dacl(user, admins, safe))
        self.assertFalse(private_dacl(user, user, None))
        for ace in [(0, 0, everyone), (0, 0x08, everyone),
                    (5, 0, user), (0, 0, 'S-1-3-0')]:
            with self.subTest(ace=ace):
                self.assertFalse(private_dacl(user, user, [*safe, ace]))


@unittest.skipUnless(os.name == 'nt', 'native Windows handles and icacls are required')
class WindowsPrivateHandleTests(unittest.TestCase):
    def setUp(self):
        from cosmesh._win_private import current_sid
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Never edit account ACLs. This newly created fixture owns its own DACL.
        self.acl(self.root, '/grant:r', '*' + current_sid() + ':(OI)(CI)F')
        self.acl(self.root, '/inheritance:r')

    def acl(self, path, *arguments):
        subprocess.run(['icacls', str(path), *arguments], check=True, capture_output=True)

    def test_profile_checked_on_its_open_handle(self):
        from cosmesh.profile import PersonalProfile
        profile = PersonalProfile(self.root / 'profile')
        before = profile.init()
        updated = profile.set({'about': '中文'}, before['version'])
        self.assertEqual(profile.read()['version'], updated['version'])
        self.acl(profile.path, '/grant', '*S-1-1-0:R')
        with self.assertRaises(Error) as error:
            profile.read()
        self.assertEqual(error.exception.code, 'PROFILE_PERMISSIONS')

    def test_profile_can_be_replaced_while_previous_snapshot_is_open(self):
        from cosmesh._platform import private_fd
        from cosmesh.profile import PersonalProfile, decode
        profile = PersonalProfile(self.root / 'profile')
        before = profile.init()
        started = threading.Event()
        def update():
            started.set()
            return profile.set({'about': '新版本'}, before['version'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            with os.fdopen(private_fd(profile.path, 'PROFILE_PERMISSIONS'), 'rb') as reader:
                written = pool.submit(update)
                self.assertTrue(started.wait(timeout=3))
                time.sleep(0.15)
                self.assertEqual(decode(reader.read())['about'], before['fields']['about'])
            updated = written.result(timeout=5)
        self.assertEqual(profile.read()['version'], updated['version'])
        self.assertEqual(profile.read()['fields']['about'], '新版本')

    def test_shared_directory_refused_before_store_init(self):
        from cosmesh.threads import initialize
        work = self.root / 'shared'
        work.mkdir()
        self.acl(work, '/grant', '*S-1-1-0:(OI)(CI)R')
        with self.assertRaises(Error) as error:
            initialize(work, 'review')
        self.assertEqual(error.exception.code, 'AUTH_DENIED')
        self.assertFalse((work / '.git').exists())

    def test_feedback_queue_round_trip_and_acl(self):
        from cosmesh.feedback import Feedback
        with patch.dict(os.environ, COSMESH_DATA_DIR=str(self.root / 'data'),
                        COSMESH_FEEDBACK_DIR=str(self.root / 'feedback')):
            feedback = Feedback()
            request = dict(category='suggestion', product='cosmesh', component='windows',
                           title='Windows test', body='测试反馈')
            draft = feedback.queue(request)
            path, row = feedback.drafts()[0]
            self.assertEqual((row['id'], row['request']['body']), (draft['id'], '测试反馈'))
            self.acl(path, '/grant', '*S-1-1-0:R')
            with self.assertRaises(Error) as error:
                feedback.drafts()
            self.assertEqual(error.exception.code, 'AUTH_DENIED')
