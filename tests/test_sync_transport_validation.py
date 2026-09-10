"""Bounded transport and import contract; run directly with stdlib unittest.

The redirect integration uses real loopback servers and urllib redirect logic.
Only HTTPS's connection class is replaced with HTTPConnection in that test;
production keeps HTTPSHandler's normal certificate verification unchanged.
"""
import http.client
import http.server
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flypath_sync as sync

TOKEN = 'synthetic-secret-token'
BASE = 'https://example.test'


class TransportValidation(unittest.TestCase):
    def call_body(self, body):
        with patch.object(sync._opener, 'open', return_value=io.BytesIO(body)):
            return sync.list_missions(BASE, TOKEN)

    def test_origins(self):
        for origin in ('http://example.test', 'https://user:pass@example.test',
                       'https://example.test/path', 'https://example.test?',
                       'https://example.test#', 'https://example.test:0',
                       'https://example.test:65536', 'https://example.test:',
                       'https://bad_host', 'https://-bad.test', 'https://127.1',
                       'https://a..test', 'https://example.test\n',
                       'https://example.test\\@evil.test', 'https://[::1%25eth0]',
                       'https://[::1]junk'):
            with self.subTest(origin=origin), patch.object(sync._opener, 'open') as send:
                with self.assertRaises(sync.FlypathSyncError):
                    sync.list_missions(origin, TOKEN)
                send.assert_not_called()
        self.assertEqual(sync._root('HTTPS://EXAMPLE.TEST.:443/'), BASE)
        self.assertEqual(sync._root('https://[0:0::1]:8443'), 'https://[::1]:8443')

    def test_bodies_and_strict_json(self):
        for body in (b'x' * (sync.MAX_RESPONSE_BYTES + 1), b'{',
                     b'{"ok":true,"missions":[],"x":NaN}',
                     b'{"ok":true,"missions":[],"x":1e999}',
                     b'[' * 1100 + b'0' + b']' * 1100,
                     b'{"ok":true,"missions":[],"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}'):
            with self.subTest(body=body[:50]), self.assertRaises(sync.FlypathSyncError):
                self.call_body(body)
        for rows in (None, {}, [None], [{'id': True}], [{'id': 1, 'name': []}],
                     [{'id': 1, 'updated_at': 2}]):
            with self.subTest(rows=rows), self.assertRaises(sync.FlypathSyncError):
                self.call_body(json.dumps({'ok': True, 'missions': rows}).encode())
        rows = [{'id': i + 1, 'name': 'Survey', 'updated_at': None} for i in range(5000)]
        self.assertEqual(self.call_body(json.dumps({'ok': True, 'missions': rows}).encode()), rows)

    def test_bounded_error_reads_and_redaction(self):
        for body in (json.dumps({'error': TOKEN}).encode(),
                     b'x' * (sync.MAX_ERROR_BYTES + 1), b'[' * 1100):
            stream = io.BytesIO(body)
            reads = []
            original = stream.read
            stream.read = lambda size: (reads.append(size), original(size))[1]
            error = urllib.error.HTTPError(BASE, 401, 'error', {}, stream)
            with patch.object(sync._opener, 'open', side_effect=error):
                with self.assertRaises(sync.FlypathSyncError) as result:
                    sync.list_missions(BASE, TOKEN)
            self.assertEqual(result.exception.status, 401)
            self.assertNotIn(TOKEN, str(result.exception))
            self.assertEqual(reads, [sync.MAX_ERROR_BYTES + 1])
        for error in (urllib.error.URLError(TOKEN), OSError(TOKEN),
                      http.client.IncompleteRead(b'partial', 100)):
            with patch.object(sync._opener, 'open', side_effect=error):
                with self.assertRaises(sync.FlypathSyncError) as result:
                    sync.list_missions(BASE, TOKEN)
                self.assertNotIn(TOKEN, str(result.exception))
        with self.assertRaises(sync.FlypathSyncError) as result:
            self.call_body(json.dumps({'ok': False, 'error': TOKEN}).encode())
        self.assertNotIn(TOKEN, str(result.exception))

    def test_maximum_mission_and_invalid_fields(self):
        mission = {'polygon': [[51.12345678901234, -114.12345678901234]] * 2000,
                   'waypoints': [[51.12345678901234, -114.12345678901234]] * 2000}
        body = json.dumps({'ok': True, 'mission': mission}).encode()
        self.assertLess(len(body), 256 * 1024)
        with patch.object(sync._opener, 'open', return_value=io.BytesIO(body)):
            self.assertEqual(sync.get_mission(BASE, TOKEN, 1), mission)
        for field, value in (('polygon', [[91, 0]]), ('polygon', [[0, 181]]),
                             ('polygon', [[True, 1]]), ('polygon', [[1, '2']]),
                             ('waypoints', [[1, 2, 3]]), ('polygon', [[1, 2]] * 2001),
                             ('settings', []), ('settings', {'speed': '8'}),
                             ('settings', {'speed': float('inf')}),
                             ('settings', {'split_count': True}),
                             ('settings', {'terrain_follow': 'false'}),
                             ('settings', {'flight_path': 'unknown'})):
            with self.subTest(field=field, value=str(value)[:70]):
                with self.assertRaises(sync.FlypathSyncError):
                    sync.validate_mission({**mission, field: value})
        sync.validate_mission({'settings': {'split_count': 1e300, 'speed': -100}})

    def test_real_redirects_never_reach_target(self):
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.server.server_port, self.headers.get('Authorization')))
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(self.server.redirect_code)
                self.send_header('Location', self.server.target)
                self.end_headers()
            do_POST = do_GET
            do_PATCH = do_GET
            def log_message(self, *args):
                pass
        class TestHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(http.client.HTTPConnection, req)
        servers = [http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        origin, target = servers
        origin_url = 'https://127.0.0.1:%d' % origin.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), TestHTTPS(), sync._NoRedirect())
        try:
            with patch.object(sync, '_opener', opener):
                for code in (301, 302, 303, 307, 308):
                    origin.redirect_code = code
                    for destination in (origin_url + '/elsewhere',
                                        'https://127.0.0.1:%d/target' % target.server_port,
                                        'http://127.0.0.1:%d/target' % target.server_port):
                        origin.target = destination
                        for call in (lambda: sync.list_missions(origin_url, TOKEN),
                                     lambda: sync.get_mission(origin_url, TOKEN, 1),
                                     lambda: sync.push_mission(origin_url, TOKEN, {}),
                                     lambda: sync.update_mission(origin_url, TOKEN, 1, 1, {})):
                            received.clear()
                            with self.assertRaises(sync.FlypathSyncError) as result:
                                call()
                            self.assertEqual(result.exception.status, code)
                            self.assertEqual(received, [(origin.server_port, 'Token ' + TOKEN)])
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
