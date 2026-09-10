"""Origin-bound personal tokens in the existing QGIS authentication vault.

Only the authcfg reference and canonical origin belong in ordinary settings.
Imports stay lazy so flypath_sync remains usable without a QGIS runtime.
"""

from functools import wraps

if __package__:
    from .flypath_sync import DEFAULT_BASE_URL, FlypathSyncError, _root
else:
    from flypath_sync import DEFAULT_BASE_URL, FlypathSyncError, _root

# Legacy settings key, not a credential.
_TOKEN = 'website_token'  # nosec B105
_BASE = 'website_base_url'
_REF = 'website_authcfg'
_ORIGIN = 'website_credential_origin'
_NAME = 'FlyPath website token'


def _storage_errors(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except FlypathSyncError:
            raise
        except Exception:
            # Backend exception text can contain credentials or connection URIs.
            raise FlypathSyncError('FlyPath credential storage failed. Check QGIS Authentication settings, then retry. Use Disconnect to forget legacy access; no plaintext fallback is used.') from None
    return checked


def _settings():
    from qgis.PyQt.QtCore import QSettings
    return QSettings('FlyPath', 'FlyPath')


def _sync(settings):
    from qgis.PyQt.QtCore import QSettings
    try:
        no_error = QSettings.Status.NoError
    except AttributeError:
        no_error = QSettings.NoError
    settings.sync()
    if settings.status() != no_error:
        raise FlypathSyncError('Could not update FlyPath credential settings. Check settings permissions and retry Disconnect.')


def _manager(unlocked=True):
    from qgis.core import QgsApplication
    manager = QgsApplication.authManager()
    if manager is None or manager.isDisabled():
        raise FlypathSyncError('QGIS authentication storage is unavailable. Enable it in QGIS settings, then reconnect. No plaintext token was saved.')
    if unlocked:
        # QGIS 3.40+ supports pluggable, potentially unencrypted backends.
        if hasattr(manager, 'authConfigurationStorageRegistry'):
            storages = manager.authConfigurationStorageRegistry().readyStorages()
            if not storages or any(not storage.isEncrypted() for storage in storages):
                raise FlypathSyncError('FlyPath requires encrypted QGIS authentication storage. Configure the QGIS vault before reconnecting.')
        if not manager.masterPasswordIsSet():
            raise FlypathSyncError('QGIS authentication storage is locked. Use Connect account to unlock it, or Disconnect to forget local access. If legacy migration failed, the old settings token remains until Disconnect succeeds.')
    return manager


@_storage_errors
def unlock_storage():
    """Prompt through QGIS only after an explicit Connect/Save action."""
    manager = _manager(unlocked=False)
    if not manager.masterPasswordIsSet() and not manager.setMasterPassword(True):
        raise FlypathSyncError('QGIS authentication storage was not unlocked. Unlock it in QGIS settings and reconnect, or use Disconnect to forget the old token.')
    _manager()


@_storage_errors
def load_base_url():
    return _root((_settings().value(_BASE, '') or '').strip() or DEFAULT_BASE_URL)


def _read(manager, reference, full=True):
    from qgis.core import QgsAuthMethodConfig
    ok, config = manager.loadAuthenticationConfig(reference, QgsAuthMethodConfig(), full)
    if not ok or config.name() != _NAME or config.method() != 'Basic':
        raise FlypathSyncError('The FlyPath vault credential could not be read. Use Disconnect and reconnect; check QGIS Authentication settings if removal fails.')
    return config


def _remove(manager, reference):
    if reference not in manager.configIds():
        return
    _read(manager, reference, full=False)
    if not manager.removeAuthenticationConfig(reference) or reference in manager.configIds():
        raise FlypathSyncError('Could not remove the FlyPath vault credential. Retry Disconnect after fixing QGIS authentication storage; revoke the token on the website if needed.')


@_storage_errors
def load_token():
    """Read the current origin's token, migrating legacy storage safely.

    Never prompts during UI initialization and never returns a legacy token
    until encrypted persistence and a full read-back have succeeded.
    """
    settings = _settings()
    origin = load_base_url()
    reference = settings.value(_REF, '') or ''
    if reference:
        if settings.value(_ORIGIN, '') != origin:
            raise FlypathSyncError('The saved FlyPath token belongs to a different origin or was disconnected. Disconnect, then connect with a token for this HTTPS website.')
        config = _read(_manager(), reference)
        if config.config('origin') != origin or not config.config('password'):
            raise FlypathSyncError('The FlyPath vault credential does not match this website. Disconnect and reconnect with the correct token.')
        # A previous migration may have persisted its reference but failed to
        # flush legacy removal. Only remove after checking the encrypted copy.
        if settings.contains(_TOKEN):
            settings.remove(_TOKEN)
            _sync(settings)
        return config.config('password')
    legacy = (settings.value(_TOKEN, '') or '').strip()
    if not legacy:
        return ''
    # Old settings have no trustworthy origin binding. Never migrate a
    # production token into a newly configured staging destination.
    if origin != _root(DEFAULT_BASE_URL):
        raise FlypathSyncError('This legacy token has no verified website binding. Use Disconnect to forget it, then reconnect with a token for this website. Rotate the old token to invalidate backups.')
    save_token(legacy)
    return load_token()


@_storage_errors
def save_token(token):
    """Persist a token with verified encryption, or forget it with ''."""
    token = (token or '').strip()
    settings = _settings()
    reference = settings.value(_REF, '') or ''
    if not token:
        # Tombstone the origin before removal so a failed delete cannot leave
        # usable access. Retain the reference until removal succeeds for retry.
        settings.remove(_TOKEN)
        settings.remove(_ORIGIN)
        _sync(settings)
        if reference:
            _remove(_manager(unlocked=False), reference)
        settings.remove(_REF)
        _sync(settings)
        return
    if '\r' in token or '\n' in token:
        raise FlypathSyncError('Paste a single-line FlyPath token.')
    origin = load_base_url()
    manager = _manager()
    _sync(settings)
    from qgis.core import QgsAuthMethodConfig
    config = QgsAuthMethodConfig()
    config.setName(_NAME)
    config.setMethod('Basic')
    config.setConfig('username', 'FlyPath')
    config.setConfig('password', token)
    # The binding is inside the encrypted payload, not merely QSettings.
    config.setConfig('origin', origin)
    if reference:
        _read(manager, reference, full=False)
        config.setId(reference)
        ok = manager.updateAuthenticationConfig(config)
    else:
        ok, config = manager.storeAuthenticationConfig(config)
    if not ok:
        raise FlypathSyncError('Could not store the FlyPath token in the QGIS vault. Reconnect after fixing storage, or use Disconnect to forget a legacy token. No plaintext fallback was used.')
    # Keep an unusable reference until verification succeeds. Failed read-back
    # must leave both legacy recovery and a way to delete the new vault entry.
    settings.setValue(_REF, config.id())
    settings.remove(_ORIGIN)
    _sync(settings)
    verified = _read(manager, config.id())
    if verified.config('password') != token or verified.config('origin') != origin:
        raise FlypathSyncError('QGIS credential verification failed. The legacy token was not removed. Use Disconnect and reconnect.')
    settings.setValue(_ORIGIN, origin)
    _sync(settings)
    settings.remove(_TOKEN)
    _sync(settings)
