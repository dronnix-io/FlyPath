"""
Tests for the website sync module (flypath_sync.py).

Pure Python, no QGIS and no real network: the sync opener's open is replaced so
each test can assert on the request that was built (URL, method, headers, body)
and on how a canned response becomes a return value or a FlypathSyncError.

Run with pytest, or directly:
    python tests/test_flypath_sync.py
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flypath_sync  # noqa: E402
from flypath_sync import (  # noqa: E402
    FlypathSyncError, WEBSITE_FINISH_ACTIONS, WEBSITE_RC_LOST_ACTIONS,
    get_mission, label_for_code, list_missions, mission_url, push_mission,
)

BASE = 'https://flypath.test'
TOKEN = 'tok-123'


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode('utf-8')

    def read(self, limit):
        return self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Urlopen:
    """Stands in for urllib.request.urlopen: records the request it was given
    and returns (or raises) what the test set up."""

    def __init__(self, result):
        self._result = result
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if isinstance(self._result, Exception):
            raise self._result
        return _FakeResponse(self._result)


# Keep the production opener method to put back after each test.
_REAL_URLOPEN = flypath_sync._opener.open


def _patch(result):
    fake = _Urlopen(result)
    flypath_sync._opener.open = fake
    return fake


def _restore():
    flypath_sync._opener.open = _REAL_URLOPEN


def _http_error(code, payload):
    body = io.BytesIO(json.dumps(payload).encode('utf-8'))
    return urllib.error.HTTPError(f'{BASE}/api/missions/', code, 'error', {}, body)


def _run(result, call):
    """Call `call()` with urlopen faked; returns (value_or_error, fake)."""
    fake = _patch(result)
    try:
        try:
            return call(), fake
        except FlypathSyncError as exc:
            return exc, fake
    finally:
        _restore()


# ── Requests are built the way the API expects ──────────────────────────────

def test_push_posts_json_with_the_token_in_the_header():
    payload = {'name': 'Plugin plan', 'drone_model': 'mini4pro'}
    result, fake = _run({'ok': True, 'id': 7, 'name': 'Plugin plan'},
                        lambda: push_mission(BASE, TOKEN, payload))

    assert result == {'ok': True, 'id': 7, 'name': 'Plugin plan'}
    assert fake.request.full_url == f'{BASE}/api/missions/'
    assert fake.request.get_method() == 'POST'
    assert fake.request.get_header('Authorization') == f'Token {TOKEN}'
    assert json.loads(fake.request.data.decode('utf-8')) == payload
    assert fake.timeout == flypath_sync.TIMEOUT_S


def test_token_never_appears_in_the_url_or_the_body():
    _, fake = _run({'ok': True, 'id': 1, 'name': 'x'},
                   lambda: push_mission(BASE, TOKEN, {'name': 'x'}))
    assert TOKEN not in fake.request.full_url
    assert TOKEN not in fake.request.data.decode('utf-8')

    _, fake = _run({'ok': True, 'missions': []},
                   lambda: list_missions(BASE, TOKEN))
    assert TOKEN not in fake.request.full_url
    assert fake.request.data is None


def test_list_returns_the_missions_and_uses_get():
    missions = [{'id': 3, 'name': 'Quarry', 'updated_at': '2026-01-02T03:04:05'}]
    result, fake = _run({'ok': True, 'missions': missions},
                        lambda: list_missions(BASE, TOKEN))

    assert result == missions
    assert fake.request.get_method() == 'GET'
    assert fake.request.full_url == f'{BASE}/api/missions/'


def test_get_mission_hits_the_detail_url_and_unwraps_the_mission():
    mission = {'id': 3, 'name': 'Quarry', 'polygon': [[51.0, -114.0]],
               'settings': {'mapping_style': '2d'}}
    result, fake = _run({'ok': True, 'mission': mission},
                        lambda: get_mission(BASE, TOKEN, 3))

    assert result == mission
    assert fake.request.full_url == f'{BASE}/api/missions/3/'
    assert fake.request.get_method() == 'GET'


def test_a_trailing_slash_on_the_base_url_does_not_double_up():
    _, fake = _run({'ok': True, 'missions': []},
                   lambda: list_missions(BASE + '/', TOKEN))
    assert fake.request.full_url == f'{BASE}/api/missions/'


# ── Failures come back as one error type, with a message fit to show ────────

def test_no_token_fails_before_any_request_is_made():
    fake = _patch({'ok': True})
    try:
        for call in (lambda: push_mission(BASE, '', {}),
                     lambda: list_missions(BASE, '   '),
                     lambda: get_mission(BASE, None, 1)):
            try:
                call()
                raise AssertionError('expected FlypathSyncError')
            except FlypathSyncError as exc:
                assert 'token' in str(exc).lower()
        assert fake.request is None, 'nothing should be sent without a token'
    finally:
        _restore()


def test_401_keeps_the_servers_message_and_status():
    error, _ = _run(_http_error(401, {'ok': False, 'error': 'Invalid or missing token.'}),
                    lambda: list_missions(BASE, TOKEN))
    assert isinstance(error, FlypathSyncError)
    assert str(error) == 'Invalid or missing token.'
    assert error.status == 401


def test_401_without_a_readable_body_still_explains_the_token():
    error, _ = _run(urllib.error.HTTPError(BASE, 401, 'no', {}, None),
                    lambda: list_missions(BASE, TOKEN))
    assert error.status == 401
    assert 'token' in str(error).lower()


def test_400_preserves_the_validation_message():
    error, _ = _run(_http_error(400, {'ok': False,
                                      'error': 'Invalid or unavailable drone model.'}),
                    lambda: push_mission(BASE, TOKEN, {'drone_model': 'nope'}))
    assert str(error) == 'Invalid or unavailable drone model.'
    assert error.status == 400


def test_network_failure_is_its_own_legible_message():
    error, _ = _run(urllib.error.URLError('getaddrinfo failed'),
                    lambda: list_missions(BASE, TOKEN))
    assert isinstance(error, FlypathSyncError)
    assert error.status is None
    assert 'connection' in str(error).lower()


def test_timeout_is_reported_not_raised_raw():
    error, _ = _run(TimeoutError('timed out'), lambda: list_missions(BASE, TOKEN))
    assert isinstance(error, FlypathSyncError)


def test_ok_false_without_an_http_error_still_fails():
    error, _ = _run({'ok': False, 'error': 'Mission not found.'},
                    lambda: get_mission(BASE, TOKEN, 9))
    assert str(error) == 'Mission not found.'


def test_an_unusable_mission_id_fails_as_a_sync_error():
    error, fake = _run({'ok': True, 'mission': {}},
                       lambda: get_mission(BASE, TOKEN, 'not-an-id'))
    assert isinstance(error, FlypathSyncError)
    assert fake.request is None


def test_a_non_http_base_url_is_refused():
    error, fake = _run({'ok': True, 'missions': []},
                       lambda: list_missions('ftp://flypath.test', TOKEN))
    assert isinstance(error, FlypathSyncError)
    assert fake.request is None


# ── Mission link and action mapping ─────────────────────────────────────────

def test_mission_url_points_at_the_planner():
    assert mission_url(12, BASE) == f'{BASE}/planner/?mission=12'
    assert mission_url(12).startswith(flypath_sync.DEFAULT_BASE_URL)


def test_action_maps_round_trip():
    for label, code in WEBSITE_FINISH_ACTIONS.items():
        assert label_for_code(WEBSITE_FINISH_ACTIONS, code) == label
    for label, code in WEBSITE_RC_LOST_ACTIONS.items():
        assert label_for_code(WEBSITE_RC_LOST_ACTIONS, code) == label
    assert label_for_code(WEBSITE_FINISH_ACTIONS, 'somethingElse') is None


def test_the_fake_urlopen_is_removed_after_a_call():
    _run({'ok': True, 'missions': []}, lambda: list_missions(BASE, TOKEN))
    assert flypath_sync._opener.open is _REAL_URLOPEN


def test_continue_mission_has_no_website_equivalent():
    # The website's RC-lost choices are goBack/landing/hover only, so the push
    # refuses instead of silently sending a different safety action.
    assert 'Continue mission' not in WEBSITE_RC_LOST_ACTIONS


def test_update_uses_patch_and_revision_without_mutating_the_payload():
    payload = {'name': 'Updated'}
    result, fake = _run({'ok': True, 'id': 3, 'revision': 5},
                         lambda: flypath_sync.update_mission(BASE, TOKEN, 3, 4, payload))
    assert fake.request.full_url == f'{BASE}/api/missions/3/'
    assert fake.request.get_method() == 'PATCH'
    assert json.loads(fake.request.data)['revision'] == 4
    assert payload == {'name': 'Updated'}
    assert result['revision'] == 5


def test_update_conflict_is_not_retried_as_a_new_mission():
    fake = _patch(_http_error(409, {'error': 'Changed elsewhere'}))
    try:
        try:
            flypath_sync.update_mission(BASE, TOKEN, 3, 4, {'name': 'Updated'})
        except FlypathSyncError as exc:
            assert exc.status == 409
        else:
            raise AssertionError('A stale save must fail')
        assert fake.request.get_method() == 'PATCH'
    finally:
        flypath_sync._opener.open = _REAL_URLOPEN


def test_updates_without_valid_revision_never_reach_the_network():
    for revision in (None, 0, -1, True, 1.5, '1'):
        result, fake = _run({'ok': True},
            lambda: flypath_sync.update_mission(BASE, TOKEN, 3, revision, {}))
        assert isinstance(result, FlypathSyncError)
        assert fake.request is None


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f'PASS  {fn.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'FAIL  {fn.__name__}: {exc}')
    print(f'\n{len(fns) - failed}/{len(fns)} passed')
    sys.exit(1 if failed else 0)
