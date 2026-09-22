"""Discover and read DJI Fly waypoint missions on drives and MTP controllers."""

import datetime
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import zipfile


_RC_EXIT_FOUND = 0
_RC_EXIT_NONE = 10
_RC_EXIT_DEVICE_NO_WP = 11
_RC_REL_PARTS = ['Android', 'data', 'dji.go.v5', 'files', 'waypoint']
_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
    r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
try:
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0
except Exception:
    _STARTUPINFO = None


def find_waypoint_on_drives():
    """Return the DJI waypoint folder on a fixed/removable drive, if present."""
    rel = os.path.join(*_RC_REL_PARTS)
    roots = []
    if sys.platform == 'win32':
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            bitmask = k32.GetLogicalDrives()
            for index in range(26):
                if not (bitmask >> index) & 1:
                    continue
                root = chr(ord('A') + index) + ':\\'
                if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (2, 3):
                    roots.append(root)
        except Exception:
            import string
            roots = [letter + ':\\' for letter in string.ascii_uppercase
                     if os.path.isdir(letter + ':\\')]
    elif sys.platform == 'linux':
        gvfs = os.path.join(
            os.getenv('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'), 'gvfs')
        try:
            for name in os.listdir(gvfs):
                if not name.startswith('mtp:'):
                    continue
                device = os.path.join(gvfs, name)
                try:
                    roots.extend(os.path.join(device, child)
                                 for child in os.listdir(device))
                except OSError:
                    continue
        except OSError:
            pass
    for root in roots:
        candidate = os.path.join(root, rel)
        try:
            if os.path.isdir(candidate):
                return candidate
        except (OSError, ValueError):
            continue
    return None


def list_missions_from_dir(waypoint_dir):
    """Return (status, missions) for a filesystem DJI waypoint folder."""
    try:
        if not os.path.isdir(waypoint_dir):
            return 'error', []
        preview_dir = os.path.join(waypoint_dir, 'map_preview')
        has_preview = os.path.isdir(preview_dir)
        preview = ({name for name in os.listdir(preview_dir)
                    if os.path.isdir(os.path.join(preview_dir, name))}
                   if has_preview else set())
        missions = []
        for uuid in os.listdir(waypoint_dir):
            folder = os.path.join(waypoint_dir, uuid)
            if not (os.path.isdir(folder) and _UUID_RE.match(uuid)):
                continue
            if has_preview and uuid not in preview:
                continue
            kmz = os.path.join(folder, uuid + '.kmz')
            metadata = _read_kmz_meta(kmz) if os.path.exists(kmz) else (0, 0, [])
            missions.append(_mission(uuid, *metadata))
        missions.sort(key=lambda item: item['create_ms'] or 0, reverse=True)
        return ('ok' if missions else 'no_mission'), missions
    except Exception:
        return 'error', []


def _mission(uuid, create_ms, waypoint_count, waypoints):
    return {
        'uuid': uuid,
        'create_ms': create_ms,
        'date_str': _format_time(create_ms),
        'n_wp': waypoint_count,
        'waypoints': waypoints,
    }


def _read_kmz_meta(kmz_path):
    try:
        with zipfile.ZipFile(kmz_path) as archive:
            template = archive.read('wpmz/template.kml').decode('utf-8', 'replace')
            try:
                waylines = archive.read('wpmz/waylines.wpml').decode('utf-8', 'replace')
            except KeyError:
                waylines = ''
        match = re.search(r'<wpml:createTime>(\d+)</wpml:createTime>', template)
        create_ms = int(match.group(1)) if match else 0
        waypoint_count = len(re.findall(r'<wpml:index>', waylines))
        waypoints = [
            (float(longitude), float(latitude))
            for longitude, latitude in re.findall(
                r'<coordinates>\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)', waylines)
        ]
        return create_ms, waypoint_count, waypoints
    except Exception:
        return 0, 0, []


def _format_time(create_ms):
    if not create_ms:
        return 'unknown date'
    try:
        return datetime.datetime.fromtimestamp(
            create_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return 'unknown date'


def _powershell():
    return os.path.join(
        os.environ.get('SystemRoot', r'C:\Windows'),
        r'System32\WindowsPowerShell\v1.0\powershell.exe')


def _run(script, *, timeout):
    temp_dir = tempfile.mkdtemp(prefix='flypath_')
    path = os.path.join(temp_dir, 'controller.ps1')
    try:
        with open(path, 'w', encoding='utf-8') as output:
            output.write(script(temp_dir))
        result = subprocess.run(  # nosec B603
            [_powershell(), '-NoProfile', '-NonInteractive', '-STA',
             '-ExecutionPolicy', 'Bypass', '-File', path],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO)
        return result, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def list_shell_children(parts):
    """Return immediate child folder names for an RC browser path."""
    if sys.platform != 'win32':
        if not parts:
            roots = ['/\0System Root', os.path.expanduser('~') + '\0Home']
            gvfs = os.path.join(
                os.getenv('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'), 'gvfs')
            try:
                roots.extend(os.path.join(gvfs, name) + '\0' + name
                             for name in os.listdir(gvfs))
            except OSError:
                pass
            return roots
        path = os.path.join(*parts)
        try:
            return [name for name in sorted(os.listdir(path))
                    if os.path.isdir(os.path.join(path, name))]
        except OSError:
            return []
    try:
        result, temp_dir = _run(
            lambda _temp: _shell_children_script(parts), timeout=40)
        return [line[2:] for line in result.stdout.splitlines()
                if line.startswith('D|')]
    except Exception:
        return []
    finally:
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _shell_children_script(parts):
    escaped = ', '.join("'" + part.replace("'", "''") + "'" for part in parts)
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        '$shell = New-Object -ComObject Shell.Application\n'
        "$folder = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
        '$parts = @(' + escaped + ')\n'
        'foreach ($p in $parts) {\n'
        '    $hit = $null\n'
        '    foreach ($i in $folder.Items()) { if ($i.Name -eq $p) { $hit = $i; break } }\n'
        '    if (-not $hit) { exit 1 }\n'
        '    $folder = $hit.GetFolder\n'
        '    if (-not $folder) { exit 1 }\n'
        '}\n'
        'foreach ($i in $folder.Items()) {\n'
        '    if ($i.IsFolder) { Write-Output ("D|" + $i.Name) }\n'
        '}\n'
    )


def list_missions_at_path(parts):
    """Return (status, missions) for a selected waypoint folder."""
    if sys.platform != 'win32':
        return list_missions_from_dir(os.path.join(*parts))
    try:
        result, temp_dir = _run(
            lambda temp: _missions_at_path_script(parts, temp), timeout=120)
        if result.returncode != 0:
            return 'error', []
        uuids = [line[len('UUID='):].strip()
                 for line in result.stdout.splitlines() if line.startswith('UUID=')]
        missions = [_mission(uuid, *_read_kmz_meta(
            os.path.join(temp_dir, uuid + '.kmz'))) for uuid in uuids]
        missions.sort(key=lambda item: item['create_ms'] or 0, reverse=True)
        return ('ok' if missions else 'no_mission'), missions
    except Exception:
        return 'error', []
    finally:
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _missions_at_path_script(parts, temp_dir):
    escaped = ', '.join("'" + part.replace("'", "''") + "'" for part in parts)
    destination = temp_dir.replace("'", "''")
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        '$shell = New-Object -ComObject Shell.Application\n'
        "$folder = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
        '$uuidPattern = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
        '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"\n'
        "$dest = $shell.Namespace('" + destination + "')\n"
        '$parts = @(' + escaped + ')\n'
        'foreach ($p in $parts) {\n'
        '    $hit = $null\n'
        '    foreach ($i in $folder.Items()) { if ($i.Name -eq $p) { $hit = $i; break } }\n'
        '    if (-not $hit) { exit 1 }\n'
        '    $folder = $hit.GetFolder\n'
        '    if (-not $folder) { exit 1 }\n'
        '}\n'
        '$wp = $folder\n'
        '$preview = @{}; $hasPreview = $false\n'
        'foreach ($c in $wp.Items()) {\n'
        "    if ($c.IsFolder -and $c.Name -eq 'map_preview') {\n"
        '        $mpf = $c.GetFolder\n'
        '        if ($mpf) { $hasPreview = $true; foreach ($pv in $mpf.Items()) { if ($pv.IsFolder) { $preview[$pv.Name] = $true } } }\n'
        '    }\n'
        '}\n'
        'foreach ($item in $wp.Items()) {\n'
        '    if ($item.IsFolder -and $item.Name -match $uuidPattern) {\n'
        '        if ($hasPreview -and -not $preview.ContainsKey($item.Name)) { continue }\n'
        "        Write-Output ('UUID=' + $item.Name)\n"
        '        $mf = $item.GetFolder\n'
        '        foreach ($f in $mf.Items()) {\n'
        '            if (-not $f.IsFolder) { $dest.CopyHere($f, 0x10); Start-Sleep -Milliseconds 1500 }\n'
        '        }\n'
        '    }\n'
        '}\n'
        'exit 0\n'
    )


def list_rc_missions():
    """Return status, waypoint path, missions, and detail for an MTP controller."""
    if sys.platform != 'win32':
        return 'not_connected', None, [], ''
    try:
        result, temp_dir = _run(_rc_list_script, timeout=120)
    except subprocess.TimeoutExpired:
        return 'error', None, [], 'Timed out while reading the RC.'
    except Exception as exc:
        return 'error', None, [], f'PowerShell error: {exc}'
    try:
        if result.returncode == _RC_EXIT_DEVICE_NO_WP:
            return 'no_mission', None, [], ''
        if result.returncode == _RC_EXIT_NONE:
            return 'not_connected', None, [], ''
        if result.returncode != _RC_EXIT_FOUND:
            return ('error', None, [],
                    result.stderr.strip() or f'Scan failed (exit {result.returncode}).')
        waypoint_path = None
        uuids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('PATH='):
                waypoint_path = line[len('PATH='):]
            elif line.startswith('UUID='):
                uuids.append(line[len('UUID='):])
        missions = [_mission(uuid, *_read_kmz_meta(
            os.path.join(temp_dir, uuid + '.kmz'))) for uuid in uuids]
        missions.sort(key=lambda item: item['create_ms'] or 0, reverse=True)
        if not missions:
            return 'no_mission', waypoint_path, [], ''
        return 'ok', waypoint_path, missions, ''
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _rc_list_script(temp_dir):
    relative = ', '.join("'" + part + "'" for part in _RC_REL_PARTS)
    joined = '\\'.join(_RC_REL_PARTS)
    destination = temp_dir.replace("'", "''")
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        '$shell = New-Object -ComObject Shell.Application\n'
        "$thisPC = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
        '$rel = @(' + relative + ')\n'
        '$uuidPattern = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
        '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"\n'
        "$dest = $shell.Namespace('" + destination + "')\n"
        '$deviceSeen = $false\n'
        'function Nav($folder, $parts) {\n'
        '    foreach ($part in $parts) {\n'
        '        $hit = $null\n'
        '        foreach ($item in $folder.Items()) {\n'
        '            if ($item.Name -eq $part) { $hit = $item; break }\n'
        '        }\n'
        '        if (-not $hit) { return $null }\n'
        '        $nf = $hit.GetFolder\n'
        '        if (-not $nf) { return $null }\n'
        '        $folder = $nf\n'
        '    }\n'
        '    return $folder\n'
        '}\n'
        'foreach ($device in $thisPC.Items()) {\n'
        '    if (-not $device.IsFolder) { continue }\n'
        "    if ($device.Path -match '^[A-Za-z]:\\\\?$') { continue }\n"
        '    $devFolder = $device.GetFolder\n'
        '    if (-not $devFolder) { continue }\n'
        "    if ($device.Name -match 'DJI|RC') { $deviceSeen = $true }\n"
        '    $roots = New-Object System.Collections.ArrayList\n'
        '    [void]$roots.Add(@($device.Name, $devFolder))\n'
        '    foreach ($vol in $devFolder.Items()) {\n'
        '        if ($vol.IsFolder) {\n'
        '            $vf = $vol.GetFolder\n'
        '            if ($vf) { [void]$roots.Add(@(($device.Name + "\\" + $vol.Name), $vf)) }\n'
        '        }\n'
        '    }\n'
        '    foreach ($root in $roots) {\n'
        '        $wp = Nav $root[1] $rel\n'
        '        if ($wp) {\n'
        '            $deviceSeen = $true\n'
        "            Write-Output ('PATH=' + $root[0] + '\\" + joined + "')\n"
        '            $preview = @{}\n'
        '            $hasPreviewDir = $false\n'
        '            $mp = $null\n'
        "            foreach ($c in $wp.Items()) { if ($c.IsFolder -and $c.Name -eq 'map_preview') { $mp = $c; break } }\n"
        '            if ($mp) {\n'
        '                $mpf = $mp.GetFolder\n'
        '                if ($mpf) {\n'
        '                    $hasPreviewDir = $true\n'
        '                    foreach ($pv in $mpf.Items()) { if ($pv.IsFolder) { $preview[$pv.Name] = $true } }\n'
        '                }\n'
        '            }\n'
        '            foreach ($item in $wp.Items()) {\n'
        '                if ($item.IsFolder -and $item.Name -match $uuidPattern) {\n'
        '                    if ($hasPreviewDir -and -not $preview.ContainsKey($item.Name)) { continue }\n'
        "                    Write-Output ('UUID=' + $item.Name)\n"
        '                    $mf = $item.GetFolder\n'
        '                    foreach ($f in $mf.Items()) {\n'
        '                        if (-not $f.IsFolder) {\n'
        '                            $dest.CopyHere($f, 0x10)\n'
        '                            Start-Sleep -Milliseconds 1500\n'
        '                        }\n'
        '                    }\n'
        '                }\n'
        '            }\n'
        f'            exit {_RC_EXIT_FOUND}\n'
        '        }\n'
        '    }\n'
        '}\n'
        f'if ($deviceSeen) {{ exit {_RC_EXIT_DEVICE_NO_WP} }}\n'
        f'exit {_RC_EXIT_NONE}\n'
    )
