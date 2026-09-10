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
import http.client
import json
import ipaddress
import math
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = 'https://flypath.io'

# The website stores at most this many points per list (its own
# MAX_MISSION_POINTS), so a longer route is refused here with a message that
# says what to do, rather than as a flat "Invalid mission waypoints." from the
# server.
MAX_MISSION_POINTS = 2000
# Two 2,000-point arrays at full float precision fit comfortably below 256 KiB.
# 2 MiB also accommodates thousands of metadata rows from the unpaginated list
# API. Do not silently truncate that list; reject oversized replies explicitly.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32

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
    missions = _call(base_url, token, '').get('missions')
    if not isinstance(missions, list):
        raise FlypathSyncError('FlyPath sent an invalid mission list.')
    for mission in missions:
        if not isinstance(mission, dict) or type(mission.get('id')) is not int or mission['id'] < 1:
            raise FlypathSyncError('FlyPath listed a mission without a usable id.')
        for key in ('name', 'updated_at'):
            if mission.get(key) is not None and not isinstance(mission[key], str):
                raise FlypathSyncError('FlyPath sent invalid mission metadata.')
    return missions


def get_mission(base_url, token, mission_id):
    """One mission's full plan: {'id', 'name', 'drone_model', 'polygon',
    'waypoints', 'settings', 'estimates'}."""
    if type(mission_id) is not int or mission_id < 1:
        raise FlypathSyncError('FlyPath listed a mission without a usable id.')
    payload = _call(base_url, token, '%s/' % mission_id)
    mission = payload.get('mission')
    if not isinstance(mission, dict):
        raise FlypathSyncError('FlyPath sent a mission this plugin could not read.')
    validate_mission(mission)
    return mission


def _validate_json(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ValueError('JSON nesting is too deep')
    if isinstance(value, dict):
        for item in value.values():
            _validate_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('JSON numbers must be finite')


def validate_mission(mission):
    """Validate downloaded fields before the dialog touches any planner state.

    Missing optional fields retain legacy defaults. Finite numeric settings
    retain the dialog's documented clamping, including fractional counts.
    Geometry topology and available drones are checked by QGIS before applying.
    """
    try:
        _validate_json(mission)
        if not isinstance(mission, dict):
            raise ValueError()
        for key in ('id', 'revision'):
            if key in mission and (type(mission[key]) is not int or mission[key] < 1):
                raise ValueError()
        for key in ('name', 'drone_model'):
            if mission.get(key) is not None and not isinstance(mission[key], str):
                raise ValueError()
        for key in ('polygon', 'waypoints'):
            points = mission.get(key, [])
            if not isinstance(points, list) or len(points) > MAX_MISSION_POINTS:
                raise ValueError()
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError()
                for value, limit in zip(point, (90, 180)):
                    if type(value) not in (int, float) or not -limit <= value <= limit:
                        raise ValueError()
        settings = mission.get('settings', {})
        if not isinstance(settings, dict):
            raise ValueError()
        enums = {'mapping_style': ('2d', 'corridor'), 'capture_mode': ('semi', 'full'),
                 'flight_path': ('straight', 'curved'),
                 'finish_action': tuple(WEBSITE_FINISH_ACTIONS.values()),
                 'rc_lost_action': tuple(WEBSITE_RC_LOST_ACTIONS.values())}
        for key, choices in enums.items():
            if key in settings and settings[key] not in choices:
                # Absent action choices historically arrive as null.
                if key not in ('finish_action', 'rc_lost_action') or settings[key] is not None:
                    raise ValueError()
        for key in ('cross_hatch', 'terrain_follow', 'split_enabled',
                    'auto_direction', 'reverse_route'):
            if key in settings and type(settings[key]) is not bool:
                raise ValueError()
        for key in ('altitude', 'speed', 'side_overlap', 'front_overlap', 'margin',
                    'direction', 'terrain_tolerance', 'split_max_wp',
                    'corridor_width', 'split_count'):
            if settings.get(key) is not None and (type(settings[key]) not in (int, float)
                                    or not math.isfinite(settings[key])):
                raise ValueError()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise FlypathSyncError('FlyPath sent invalid mission geometry or settings.') from None


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
    """Canonical HTTPS origin shared with credential binding; never a URL path."""
    try:
        value = base_url or DEFAULT_BASE_URL
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError()
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or '?' in value or '#' in value or '\\' in value):
            raise ValueError()
        host = parsed.hostname
        if not host or '%' in host:
            raise ValueError()
        if not re.fullmatch(r'(?:\[[0-9a-fA-F:.]+\]|[^:\[\]]+)(?::[0-9]+)?', parsed.netloc):
            raise ValueError()
        if ':' in host:
            host = '[' + ipaddress.IPv6Address(host).compressed + ']'
        else:
            host = host.encode('idna').decode('ascii').lower().rstrip('.')
            if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                                      for part in host.split('.')):
                raise ValueError()
            if re.fullmatch(r'[0-9.]+', host):
                host = str(ipaddress.IPv4Address(host))
        port = parsed.port
        if parsed.netloc.endswith(':') or port == 0:
            raise ValueError()
        return 'https://' + host + (':%d' % port if port not in (None, 443) else '')
    except (ValueError, UnicodeError):
        raise FlypathSyncError('The FlyPath address must be a valid HTTPS origin without credentials, path, query or fragment.') from None


def _api_url(base_url, path):
    return '%s/api/missions/%s' % (_root(base_url), path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def _read_json(response, limit):
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('response exceeds the size limit')
    payload = json.loads(body.decode('utf-8'))
    _validate_json(payload)
    return payload


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
        try:
            body = json.dumps(data, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, RecursionError):
            raise FlypathSyncError('This mission could not be encoded as JSON.') from None
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method or ('POST' if body else 'GET'))
    try:
        with _opener.open(request, timeout=TIMEOUT_S) as response:
            payload = _read_json(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            message = _server_message(exc)
        finally:
            exc.close()
        raise FlypathSyncError(message.replace(token.strip(), '[redacted]'), status=exc.code) from None
    except urllib.error.URLError as exc:
        raise FlypathSyncError(
            'Could not reach FlyPath (%s).\n\nCheck your internet connection '
            'and try again.' % str(exc.reason).replace(token.strip(), '[redacted]')) from None
    except (OSError, ValueError, RecursionError, http.client.HTTPException) as exc:
        raise FlypathSyncError('FlyPath did not answer properly (%s).' %
                               str(exc).replace(token.strip(), '[redacted]')) from None

    if not isinstance(payload, dict) or payload.get('ok') is not True:
        raise FlypathSyncError(
            (_error_text(payload) or 'FlyPath rejected the request.').replace(token.strip(), '[redacted]'))
    return payload


def _server_message(exc):
    """The server's own error text for a non-2xx reply, so a validation
    rejection reads the same in QGIS as it would in the browser."""
    try:
        payload = _read_json(exc, MAX_ERROR_BYTES)
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
