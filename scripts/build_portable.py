"""Build a Windows x64 portable ZIP from an explicit allowlist of resources."""
from pathlib import Path
import hashlib
import json
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_runtime import ASSETS
from akuz_version import __version__


def main():
    if sys.platform != 'win32' or platform.architecture()[0] != '64bit':
        raise SystemExit('Run this build with 64-bit Python on Windows.')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--console', '--name', 'AKUZLogExplorer',
               '--distpath', str(ROOT/'dist'), '--workpath', str(ROOT/'build'),
               '--specpath', str(ROOT/'build'), '--noupx',
               '--collect-submodules', 'paramiko', '--python-option', 'X utf8']
    for name in ASSETS:
        command += ['--add-data', str(ROOT/name)+':.']
    command.append(str(ROOT/'akuz_app.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    output = ROOT/'dist'
    executable = output/'AKUZLogExplorer.exe'
    archive = output/'AKUZLogExplorer-windows-x64.zip'
    commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    packages = subprocess.check_output([sys.executable,'-m','pip','freeze'], text=True).splitlines()
    manifest = json.dumps(dict(version=__version__, commit=commit, platform='Windows x64',
                               python=platform.python_version(), packages=packages), indent=2)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        package.write(executable, 'AKUZLogExplorer/AKUZLogExplorer.exe')
        # Only the tracked example is packaged, never the local live config.
        package.write(ROOT/'ConnectConf.example.cfg', 'AKUZLogExplorer/ConnectConf.cfg')
        package.write(ROOT/'README_PORTABLE.md', 'AKUZLogExplorer/README_PORTABLE.md')
        package.writestr('AKUZLogExplorer/BUILD_INFO.json', manifest+'\n')
    sums=[]
    for file in (executable, archive):
        with file.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        sums.append(digest+'  '+file.name)
    (output/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n', encoding='ascii')
    print(archive)


if __name__ == '__main__':
    main()
