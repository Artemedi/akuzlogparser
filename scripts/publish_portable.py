"""Upload verified binaries to a draft release, without Actions artifact storage."""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from akuz_version import __version__


def main():
    commit=os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[0-9a-f]{40}',commit):
        raise SystemExit('Expected the exact build commit SHA')
    files=[ROOT/'dist'/name for name in
           ('AKUZLogExplorer.exe','AKUZLogExplorer-windows-x64.zip','SHA256SUMS.txt')]
    if not all(file.is_file() for file in files):
        raise SystemExit('Missing build output')
    subprocess.run(['gh','release','create','v'+__version__,*(str(file) for file in files),
                    '--repo',os.environ['GITHUB_REPOSITORY'], '--target',commit,
                    '--draft','--title','AKUZ Log Explorer '+__version__+' — Windows portable',
                    '--notes-file',str(ROOT/'RELEASE_NOTES.md')],cwd=ROOT,check=True)


if __name__=='__main__':
    main()
