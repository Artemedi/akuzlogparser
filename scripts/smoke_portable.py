"""Verify the distributed ZIP and its EXE without Python or pip in the child PATH."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


def main():
    archive = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='AKUZ portable ') as scratch:
        root = Path(scratch)/'Проверка без Python'
        root.mkdir()
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            expected = {'AKUZLogExplorer/'+name for name in (
                'AKUZLogExplorer.exe','ConnectConf.cfg','README_PORTABLE.md','BUILD_INFO.json')}
            if names != expected:
                raise RuntimeError('Unexpected portable package contents: '+repr(names))
            package.extractall(root)
        app = root/'AKUZLogExplorer'
        executable = app/'AKUZLogExplorer.exe'
        env = os.environ.copy()
        env['PATH'] = str(Path(os.environ['SystemRoot'])/'System32')
        env.pop('PYTHONHOME',None)
        env.pop('PYTHONPATH',None)
        result = subprocess.run([str(executable),'--self-test'], cwd=scratch, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        if result.returncode:
            raise RuntimeError("EXE self-test failed:\n"+result.stdout+result.stderr)
        check = json.loads(result.stdout.strip())
        if not check['ok'] or Path(check['app_root']) != app.resolve():
            raise RuntimeError('Frozen app does not use the executable directory')
        config = app/'ConnectConf.cfg'
        config.write_bytes(config.read_bytes()+b'\n# preserve existing operator config\n')
        saved_config = config.read_bytes()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        base = 'http://127.0.0.1:'+str(port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (root/'smoke-output.txt').open('wb') as output:
            process = subprocess.Popen([str(executable),'--no-browser','--port',str(port)],
                                       cwd=scratch, env=env, stdout=output, stderr=output)
            try:
                deadline=time.monotonic()+60
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('EXE exited during startup: '+
                                           (root/'smoke-output.txt').read_text(encoding='utf-8',errors='replace'))
                    try:
                        with opener.open(base+'/api/status',timeout=2) as response:
                            if response.status == 200:
                                break
                    except (OSError,urllib.error.URLError):
                        if time.monotonic()>=deadline:
                            raise RuntimeError('EXE startup timed out')
                        time.sleep(.2)
                for route in ('/','/app_controls.js','/errors.html','/data/analytics.js',
                              '/api/analytics/sources'):
                    with opener.open(base+route,timeout=10) as response:
                        if response.status != 200 or not response.read():
                            raise RuntimeError('Missing portable route: '+route)
                try:
                    opener.open(base+'/ConnectConf.cfg',timeout=5)
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                else:
                    raise RuntimeError('Config must not be served over HTTP')
                # Exercise the packaged LOCAL path source end-to-end, without SSH
                # credentials, network shares, or Python on the child PATH.
                source_folder = root / 'Локальные журналы'
                source_folder.mkdir()
                local_log = source_folder / '20260923_smoke.log'
                original = ('12:00:00.000,AKUZ,s1,user: '
                            'System.InvalidOperationException: Failed patient 1234\n'
                            ' at AKUZ.Serialize()\n').encode('utf-8')
                local_log.write_bytes(original)
                for endpoint, local_path in (('/api/list', source_folder),
                                             ('/api/fetch', local_log)):
                    payload = json.dumps(dict(source='local', local_path=str(local_path))).encode('utf-8')
                    req = urllib.request.Request(base + endpoint, data=payload, method='POST',
                         headers={'Origin':base, 'Content-Type':'application/json'})
                    with opener.open(req, timeout=15) as response:
                        if response.status != 202:
                            raise RuntimeError('Local source refused: '+endpoint)
                    until = time.monotonic() + 45
                    while True:
                        with opener.open(base+'/api/status',timeout=5) as response:
                            status = json.load(response)
                        if not status['busy']:
                            if status.get('error'):
                                raise RuntimeError('Local source: '+status['error'])
                            break
                        if time.monotonic() > until:
                            raise RuntimeError('Local source timed out: '+endpoint)
                        time.sleep(.1)
                if status['result']['reports'][0]['events'] != 1:
                    raise RuntimeError('Local file failed to produce a report')
                if local_log.read_bytes() != original:
                    raise RuntimeError('Local input file was modified')
                if not (app/'cache'/'inventory.json').is_file():
                    raise RuntimeError('Local source did not register the report')
                if config.read_bytes() != saved_config:
                    raise RuntimeError('Operator config was overwritten')
                if not (app/'cache'/'error_analytics.sqlite').is_file():
                    raise RuntimeError('Cache was not stored beside the EXE')
                if not (app/'data'/'analytics.js').is_file():
                    raise RuntimeError('Analytics was not stored beside the EXE')
                print('Portable ZIP passed: offline parsing, native crypto, localhost UI, config preservation, Unicode path, no Python in PATH.')
            finally:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
