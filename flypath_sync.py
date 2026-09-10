"""
flypath_sync.py
---------------
Talks to the FlyPath website's plugin API: push the mission just planned in QGIS
into the pilot's account as a new draft, list the missions already there, and
pull one back down to keep planning it locally.

Pure Python (stdlib `urllib.request` + `json`, the same approach terrain.py uses
for the elevation tiles), with no QGIS or Qt import at module level, so it is
unit-testable outside a QGIS runtime like the rest of tests/. The dialog layer
owns every message box; this module only raises FlypathSyncError with a message
that is already fit to show to the pilot.

The personal access token is pasted once from the website's profile page. It is
kept in QSettings on this machine and only ever leaves it as an
`Authorization: Token <value>` request header — never in a URL, a body, a log
line, the QGIS project file or an exported mission.
"""

import hashlib
import json
import urllib.error
import urllib.request

DEFAULT_BASE_URL = 'https://flypath.io'

# The website stores at most this many points per list (its own
# MAX_MISSION_POINTS), so a longer route is refused here with a message that
# says what to do, rather than as a flat "Invalid mission waypoints." from the
# server.
MAX_MISSION_POINTS = 2000

# Network calls run on the UI thread (as the DEM fetches already do), so the
# timeout is what bounds how long QGIS can look frozen on a dead connection.
TIMEOUT_S = 20

_SETTINGS_ORG = 'FlyPath'
_SETTINGS_APP = 'FlyPath'
# The QSettings key the token is stored under, and the text shown when none is
# set. Both name a secret without being one, hence the scanner suppressions.
_TOKEN_KEY = 'website_token'   # nosec B105
_BASE_URL_KEY = 'website_base_url'

NO_TOKEN_MESSAGE = (           # nosec B105
    'No FlyPath token set.\n\n'
    'Sign in at flypath.io, open Profile, generate a plugin token, and paste '
    'it here.'
)

# Plugin label -> the website's own code for the same action. The plugin's
# 'Continue mission' RC-lost choice has no website equivalent, so it is absent
# here and the push refuses rather than quietly sending a different action.
WEBSITE_FINISH_ACTIONS = {
    'Return to Home':        'goHome',
    'Hover in place':        'noAction',
    'Land at last waypoint': 'autoLand',
}
WEBSITE_RC_LOST_ACTIONS = {
    'Return to Home':   'goBack',
    'Hover in place':   'hover',
    'Land immediately': 'landing',
}


class FlypathSyncError(Exception):
    """A push/pull failed. `message` is written to be shown to the user as-is;
    `status` is the HTTP status when the server answered, else None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def label_for_code(website_codes, code):
    """Reverse of the WEBSITE_* maps: the plugin's label for a website code, or
    None when the website sent one this plugin does not offer."""
    for label, value in website_codes.items():
        if value == code:
            return label
    return None


def mission_url(mission_id, base_url=DEFAULT_BASE_URL):
    """Where the pilot can open a pushed mission on the website."""
    return '%s/planner/?mission=%s' % (_root(base_url), mission_id)


def push_mission(base_url, token, payload):
    """POST a mission into the account the token belongs to. Always creates a
    new draft (the API drops any `id`), so a push can never overwrite a mission
    that already exists there. Returns the API's {'ok', 'id', 'name'}."""
    return _call(base_url, token, '', data=payload)


def account_key(base_url, token):
    """Session-only scope for a mission link; never retain the raw token."""
    return (_root(base_url), hashlib.sha256(token.strip().encode('utf-8')).hexdigest())


def update_mission(base_url, token, mission_id, revision, payload):
    """Update a linked mission; 409 leaves both versions intact."""
    if type(mission_id) is not int or mission_id < 1:
        raise FlypathSyncError('This mission has no usable FlyPath id. Load it again.')
    if type(revision) is not int or revision < 1:
        raise FlypathSyncError('This mission has no revision. Update the website and load it again before saving changes.')
    return _call(base_url, token, '%s/' % mission_id,
                 data={**payload, 'revision': revision}, method='PATCH')


def list_missions(base_url, token):
    """The account's missions as [{'id', 'name', 'updated_at'}, ...], newest
    first (the API's own order)."""
    return _call(base_url, token, '').get('missions') or []


def get_mission(base_url, token, mission_id):
    """One mission's full plan: {'id', 'name', 'drone_model', 'polygon',
    'waypoints', 'settings', 'estimates'}."""
    try:
        mission_id = int(mission_id)
    except (TypeError, ValueError):
        raise FlypathSyncError('FlyPath listed a mission without a usable id.')
    payload = _call(base_url, token, '%s/' % mission_id)
    mission = payload.get('mission')
    if not isinstance(mission, dict):
        raise FlypathSyncError('FlyPath sent a mission this plugin could not read.')
    return mission


# ── Token storage (QSettings, imported lazily so the module stays QGIS-free) ──

def load_token():
    """The token stored on this machine, or '' when none has been pasted yet."""
    from qgis.PyQt.QtCore import QSettings
    return (QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_TOKEN_KEY, '') or '').strip()


def save_token(token):
    """Store (or, with an empty value, forget) the token for this machine."""
    from qgis.PyQt.QtCore import QSettings
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_TOKEN_KEY, (token or '').strip())


def load_base_url():
    """The site to talk to: flypath.io, unless a different address is set in
    QSettings under 'website_base_url' (a staging or local server, so testing
    against one needs no code change)."""
    from qgis.PyQt.QtCore import QSettings
    stored = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_BASE_URL_KEY, '')
    return (stored or '').strip() or DEFAULT_BASE_URL


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _root(base_url):
    return (base_url or DEFAULT_BASE_URL).strip().rstrip('/')


def _api_url(base_url, path):
    url = '%s/api/missions/%s' % (_root(base_url), path)
    if not url.startswith(('https://', 'http://')):
        raise FlypathSyncError(
            'The FlyPath address must be an http:// or https:// one (got %r).'
            % _root(base_url))
    return url


def _call(base_url, token, path, data=None, method=None):
    """One API call. A body means POST, no body means GET. Returns the decoded
    JSON object, raising FlypathSyncError on anything else."""
    if not (token or '').strip():
        raise FlypathSyncError(NO_TOKEN_MESSAGE)

    url = _api_url(base_url, path)
    headers = {'Authorization': 'Token %s' % token.strip(),
               'Accept': 'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method or ('POST' if body else 'GET'))
    try:
        # _api_url has already rejected anything but an http(s) address.
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # nosec B310
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise FlypathSyncError(_server_message(exc), status=exc.code)
    except urllib.error.URLError as exc:
        raise FlypathSyncError(
            'Could not reach FlyPath (%s).\n\nCheck your internet connection '
            'and try again.' % (exc.reason,))
    except (OSError, ValueError) as exc:      # timeout, dropped read, bad JSON
        raise FlypathSyncError('FlyPath did not answer properly (%s).' % (exc,))

    if not isinstance(payload, dict) or not payload.get('ok'):
        raise FlypathSyncError(
            _error_text(payload) or 'FlyPath rejected the request.')
    return payload


def _server_message(exc):
    """The server's own error text for a non-2xx reply, so a validation
    rejection reads the same in QGIS as it would in the browser."""
    try:
        payload = json.loads(exc.read().decode('utf-8'))
    except Exception:                          # noqa: BLE001 - any unreadable body
        payload = None
    text = _error_text(payload)
    if text:
        return text
    if exc.code == 401:
        return ('FlyPath rejected the token. Generate a new one on the website '
                'profile page and paste it in again.')
    return 'FlyPath returned an error (HTTP %s).' % exc.code


def _error_text(payload):
    if isinstance(payload, dict):
        error = payload.get('error')
        if isinstance(error, str) and error.strip():
            return error.strip()
    return ''
