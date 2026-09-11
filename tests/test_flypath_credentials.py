"""Isolated real-QGIS vault and credential UI checks; no network or user profile.

Run with the QGIS Python launcher (python-qgis.bat, the OSGeo4W shell, or the
equivalent), which provides the QGIS libraries for the installed version. When
QGIS cannot be imported the suite skips, so it is portable across QGIS versions
and machines. No QGIS installation or user settings are changed.
"""

import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch


class CredentialStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.temp = tempfile.TemporaryDirectory(prefix='credential-test-', dir=cls.repo,
                                              ignore_cleanup_errors=True)
        cls.profile = Path(cls.temp.name)
        cls.environment = patch.dict(os.environ, {
            'QT_QPA_PLATFORM': 'offscreen',
            'QGIS_AUTH_DB_DIR_PATH': str(cls.profile),
            'QGIS_CUSTOM_CONFIG_PATH': str(cls.profile),
            'QGIS_AUTH_PASSWORD_FILE': '',
            'QGIS_AUTH_DB_URI': '',
        })
        cls.environment.start()
        # Run this suite with the QGIS Python launcher (python-qgis.bat, the
        # OSGeo4W shell, or the equivalent), which sets up the QGIS libraries
        # for whatever version is installed. When QGIS cannot be imported, skip
        # rather than error, so the suite is portable across QGIS versions and
        # machines and stays green where QGIS is absent.
        try:
            from qgis.core import QgsApplication, QgsAuthMethodConfig
            from qgis.PyQt.QtCore import QSettings
        except ImportError as exc:
            cls.environment.stop()
            cls.temp.cleanup()
            raise unittest.SkipTest(
                'Requires the QGIS Python environment (run via the QGIS Python '
                'launcher / OSGeo4W shell)') from exc
        if QgsApplication.instance() is not None:
            cls.environment.stop()
            cls.temp.cleanup()
            raise unittest.SkipTest('Run the vault suite in its own process, never an existing QGIS session')
        try:
            ini = QSettings.Format.IniFormat
            scopes = (QSettings.Scope.UserScope, QSettings.Scope.SystemScope)
        except AttributeError:
            ini = QSettings.IniFormat
            scopes = (QSettings.UserScope, QSettings.SystemScope)
        QSettings.setDefaultFormat(ini)
        for scope in scopes:
            QSettings.setPath(ini, scope, str(cls.profile))
        cls.app = QgsApplication([], False, str(cls.profile))
        cls.app.initQgis()
        cls.manager = QgsApplication.authManager()
        assert Path(cls.manager.authenticationDatabasePath()).resolve().is_relative_to(cls.profile)
        cls.manager.setPasswordHelperEnabled(False)
        assert cls.manager.setMasterPassword('synthetic-test-master-password', True)
        cls.config_type = QgsAuthMethodConfig
        cls.settings = QSettings(str(cls.profile / 'flypath.ini'), ini)
        sys.path.insert(0, str(cls.repo.parent))
        cls.credentials = importlib.import_module(cls.repo.name + '.flypath_credentials')
        cls.sync = importlib.import_module(cls.repo.name + '.flypath_sync')
        cls.settings_patch = patch.object(cls.credentials, '_settings', return_value=cls.settings)
        cls.settings_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.settings_patch.stop()
        cls.app.exitQgis()
        cls.environment.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.settings.clear()
        self.settings.sync()
        self.manager.setMasterPassword('synthetic-test-master-password', True)
        for reference in self.manager.configIds():
            self.manager.removeAuthenticationConfig(reference)

    def test_encrypted_migration_reload_and_disconnect_while_locked(self):
        token = 'synthetic-legacy-token-for-vault-test'
        self.settings.setValue('website_token', token)
        self.assertEqual(self.credentials.load_token(), token)
        self.assertFalse(self.settings.contains('website_token'))
        reference = self.settings.value('website_authcfg')
        self.assertTrue(reference)
        self.settings.sync()
        self.assertNotIn(token.encode(), (self.profile / 'flypath.ini').read_bytes())
        self.assertNotIn(token.encode(), Path(self.manager.authenticationDatabasePath()).read_bytes())
        self.manager.clearMasterPassword()
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'locked'):
            self.credentials.load_token()
        self.assertTrue(self.manager.setMasterPassword('synthetic-test-master-password', True))
        self.assertEqual(self.credentials.load_token(), token)
        self.manager.clearMasterPassword()
        self.credentials.save_token('')
        self.assertNotIn(reference, self.manager.configIds())
        self.assertFalse(self.settings.contains('website_authcfg'))
        self.assertEqual(self.credentials.load_token(), '')

    def test_origin_binding_in_encrypted_payload_and_replacement(self):
        self.credentials.save_token('synthetic-production')
        first = self.settings.value('website_authcfg')
        self.credentials.save_token('synthetic-rotated')
        self.assertEqual(self.manager.configIds(), [first])
        self.assertEqual(self.credentials.load_token(), 'synthetic-rotated')
        self.settings.setValue('website_base_url', 'https://staging.example.test')
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'different origin'):
            self.credentials.load_token()
        self.settings.setValue('website_credential_origin', 'https://staging.example.test')
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'does not match'):
            self.credentials.load_token()
        self.credentials.save_token('')
        self.credentials.save_token('synthetic-staging')
        self.assertEqual(self.credentials.load_token(), 'synthetic-staging')

    def test_failed_migration_retains_legacy_and_can_be_forgotten(self):
        self.settings.setValue('website_token', 'synthetic-legacy')
        self.manager.clearMasterPassword()
        with self.assertRaises(self.sync.FlypathSyncError):
            self.credentials.load_token()
        self.assertEqual(self.settings.value('website_token'), 'synthetic-legacy')
        self.assertFalse(self.settings.contains('website_authcfg'))
        self.credentials.save_token('')
        self.assertFalse(self.settings.contains('website_token'))

    def test_custom_origin_cannot_claim_unbound_legacy_token(self):
        self.settings.setValue('website_token', 'synthetic-legacy')
        self.settings.setValue('website_base_url', 'https://staging.example.test')
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'no verified website binding'):
            self.credentials.load_token()
        self.assertFalse(self.manager.configIds())
        self.credentials.save_token('')
        self.assertFalse(self.settings.contains('website_token'))

    def test_failed_readback_keeps_legacy_and_reference_for_disconnect(self):
        self.settings.setValue('website_token', 'synthetic-legacy')
        with patch.object(self.credentials, '_read', side_effect=self.sync.FlypathSyncError('read failed')):
            with self.assertRaises(self.sync.FlypathSyncError):
                self.credentials.load_token()
        self.assertEqual(self.settings.value('website_token'), 'synthetic-legacy')
        self.assertTrue(self.settings.contains('website_authcfg'))
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'disconnected'):
            self.credentials.load_token()
        self.credentials.save_token('')
        self.assertFalse(self.settings.contains('website_token'))
        self.assertFalse(self.manager.configIds())

    def test_failed_disconnect_disables_access_and_retains_reference_for_retry(self):
        self.credentials.save_token('synthetic-token')
        reference = self.settings.value('website_authcfg')
        self.settings.setValue('website_token', 'synthetic-legacy')
        with patch.object(self.credentials, '_remove', side_effect=self.sync.FlypathSyncError('delete failed')):
            with self.assertRaises(self.sync.FlypathSyncError):
                self.credentials.save_token('')
        self.assertFalse(self.settings.contains('website_token'))
        self.assertEqual(self.settings.value('website_authcfg'), reference)
        with self.assertRaisesRegex(self.sync.FlypathSyncError, 'disconnected'):
            self.credentials.load_token()
        self.credentials.save_token('')
        self.assertNotIn(reference, self.manager.configIds())

    def test_backend_failure_is_redacted_and_no_plaintext_is_saved(self):
        with patch.object(self.credentials, '_manager', side_effect=RuntimeError('secret-token-in-backend-error')):
            with self.assertRaises(self.sync.FlypathSyncError) as error:
                self.credentials.save_token('synthetic-token')
        self.assertNotIn('secret-token', str(error.exception))
        self.assertFalse(self.settings.contains('website_token'))

    def test_unencrypted_backend_is_rejected(self):
        manager = SimpleNamespace(
            isDisabled=lambda: False,
            authConfigurationStorageRegistry=lambda: SimpleNamespace(
                readyStorages=lambda: [SimpleNamespace(isEncrypted=lambda: False)]))
        with patch('qgis.core.QgsApplication.authManager', return_value=manager):
            with self.assertRaisesRegex(self.sync.FlypathSyncError, 'requires encrypted'):
                self.credentials.save_token('synthetic-token')
        self.assertFalse(self.manager.configIds())

    def test_failed_vault_write_preserves_legacy(self):
        self.settings.setValue('website_token', 'synthetic-legacy')
        from unittest.mock import Mock
        manager = Mock(wraps=self.manager)
        manager.storeAuthenticationConfig.return_value = (False, self.config_type())
        with patch.object(self.credentials, '_manager', return_value=manager):
            with self.assertRaisesRegex(self.sync.FlypathSyncError, 'Could not store'):
                self.credentials.load_token()
        self.assertEqual(self.settings.value('website_token'), 'synthetic-legacy')
        self.assertFalse(self.settings.contains('website_authcfg'))

    def test_failed_settings_flush_does_not_claim_migration(self):
        self.settings.setValue('website_token', 'synthetic-legacy')
        with patch.object(self.credentials, '_sync', side_effect=self.sync.FlypathSyncError('settings failed')):
            with self.assertRaises(self.sync.FlypathSyncError):
                self.credentials.load_token()
        self.assertEqual(self.settings.value('website_token'), 'synthetic-legacy')
        self.assertFalse(self.manager.configIds())

    def test_unlock_cancellation_is_a_clear_error(self):
        from unittest.mock import Mock
        manager = Mock(wraps=self.manager)
        manager.masterPasswordIsSet.return_value = False
        manager.setMasterPassword.return_value = False
        with patch.object(self.credentials, '_manager', return_value=manager):
            with self.assertRaisesRegex(self.sync.FlypathSyncError, 'not unlocked'):
                self.credentials.unlock_storage()

    def test_credential_dialog_handles_save_failure_without_returning_token(self):
        from qgis.PyQt.QtWidgets import QWidget
        dialog = importlib.import_module(self.repo.name + '.flypath_dialog')
        planner = QWidget()
        planner._update_web_buttons = lambda: None
        with patch.object(self.credentials, 'unlock_storage'), \
             patch.object(self.sync, 'load_token', return_value=''), \
             patch.object(self.sync, 'save_token', side_effect=self.sync.FlypathSyncError('write failed')), \
             patch.object(dialog.QInputDialog, 'getText', return_value=('synthetic-token', True)), \
             patch.object(dialog.QMessageBox, 'warning') as warning:
            self.assertIsNone(dialog.FlyPathDialog._web_token(planner))
            self.assertIn('write failed', warning.call_args.args)
        planner.close()

    def test_token_prompt_names_origin_and_refuses_changed_destination(self):
        from qgis.PyQt.QtWidgets import QWidget
        dialog = importlib.import_module(self.repo.name + '.flypath_dialog')
        planner = QWidget()
        planner._update_web_buttons = lambda: None
        with patch.object(self.credentials, 'unlock_storage'), \
             patch.object(self.sync, 'load_token', return_value=''), \
             patch.object(self.sync, 'load_base_url', side_effect=[
                 'https://staging.example.test', 'https://staging.example.test',
                 'https://other.example.test']), \
             patch.object(self.sync, 'save_token') as save, \
             patch.object(dialog.QInputDialog, 'getText', return_value=('synthetic-token', True)) as prompt, \
             patch.object(dialog.QMessageBox, 'warning'):
            self.assertIsNone(dialog.FlyPathDialog._web_token(planner))
            self.assertIn('https://staging.example.test', prompt.call_args.args[2])
            save.assert_not_called()
        planner.close()

    def test_event_processing_cannot_switch_request_origin_or_token(self):
        from qgis.PyQt.QtWidgets import QWidget
        from unittest.mock import Mock
        dialog = importlib.import_module(self.repo.name + '.flypath_dialog')
        planner = QWidget()
        planner._update_web_buttons = lambda: None
        planner._web_token = lambda: 'synthetic-original'
        for changed_origin, changed_token in (
                ('https://other.example.test', 'synthetic-original'),
                ('https://flypath.io', 'synthetic-replacement')):
            work = Mock()
            with patch.object(self.sync, 'load_base_url', side_effect=['https://flypath.io', changed_origin]), \
                 patch.object(self.sync, 'load_token', return_value=changed_token), \
                 patch.object(dialog.QApplication, 'processEvents'), \
                 patch.object(dialog.QMessageBox, 'warning'):
                self.assertIsNone(dialog.FlyPathDialog._run_web(planner, 'Test', work))
                work.assert_not_called()
        planner.close()

    def test_locked_library_initializes_and_disconnect_clears_mission_link(self):
        from qgis.PyQt.QtWidgets import QWidget
        library_module = importlib.import_module(self.repo.name + '.flypath_library')
        dialog_module = importlib.import_module(self.repo.name + '.flypath_dialog')
        planner = QWidget()
        planner._website_link = {'account': ('https://flypath.io', 'old'), 'name': 'Test'}
        planner._current_website_link = MethodType(dialog_module.FlyPathDialog._current_website_link, planner)
        self.settings.setValue('website_token', 'synthetic-legacy')
        self.manager.clearMasterPassword()
        with patch.object(self.sync, 'load_token', self.credentials.load_token), \
             patch.object(self.sync, 'save_token', self.credentials.save_token), \
             patch.object(self.sync, 'load_base_url', self.credentials.load_base_url):
            library = library_module.MissionLibrary(planner)
            self.assertIn('locked', library.status.text())
            self.assertFalse(library.disconnect_button.isHidden())
            self.assertFalse(library.refresh_button.isEnabled())
            self.assertIsNone(planner._website_link)
            library.disconnect_account()
            self.assertFalse(self.settings.contains('website_token'))
            self.assertIn('still valid', library.status.text())
            library.close()
        planner.close()


if __name__ == '__main__':
    unittest.main()
