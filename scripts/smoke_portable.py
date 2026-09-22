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
