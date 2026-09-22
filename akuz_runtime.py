"""Locate bundled resources and keep portable user data beside the executable."""
from pathlib import Path
import os
import shutil
import sys
import tempfile

DOCUMENTS = ('README.md', 'README_PORTABLE.md', 'README_PROJECT.md',
             'README_EXPLORER.md', 'README_START_HERE.md', 'README_ANALYTICS.md',
             'CHANGELOG.md', 'CHANGELOG_v4.md', 'CHANGELOG_v4_1.md', 'CHANGELOG_v4_2.md')

ASSETS = (
    'index.html', 'event.html', 'errors.html', 'style.css', 'common.js',
    'index.js', 'event.js', 'errors.js', 'app_controls.js',
    'ConnectConf.example.cfg',
) + DOCUMENTS


def app_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def install_assets(root, bundle):
    """Refresh only packaged UI files; never overwrite config, logs or reports."""
    root, bundle = Path(root), Path(bundle)
    for name in ASSETS:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix='.akuz-ui-', delete=False) as target:
                temporary = Path(target.name)
                with (bundle/name).open('rb') as source:
                    shutil.copyfileobj(source, target)
            os.replace(temporary, root/name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    # Exclusive creation preserves an existing operator configuration.
    try:
        with (root/'ConnectConf.cfg').open('xb') as target:
            target.write((bundle/'ConnectConf.example.cfg').read_bytes())
    except FileExistsError:
        pass


def prepare_runtime():
    root = app_root()
    if getattr(sys, 'frozen', False):
        install_assets(root, Path(__file__).resolve().parent)
    return root
