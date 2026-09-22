"""Portable paths, asset upgrades, and preservation of operator-owned files."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

import akuz_runtime as runtime


class PortableRuntimeTests(unittest.TestCase):
    def test_frozen_root_is_executable_folder_not_cwd_or_extraction_folder(self):
        with TemporaryDirectory() as scratch:
            folder=Path(scratch)/'Папка приложения'
            folder.mkdir()
            executable=folder/'AKUZLogExplorer.exe'
            with patch.object(sys,'frozen',True,create=True), patch.object(sys,'executable',str(executable)):
                self.assertEqual(runtime.app_root(),folder.resolve())

    def test_asset_upgrade_preserves_config_and_data(self):
        with TemporaryDirectory() as scratch:
            root=Path(scratch)/'app'
            bundle=Path(scratch)/'bundle'
            root.mkdir();bundle.mkdir()
            for name in runtime.ASSETS:
                (bundle/name).write_text('new '+name,encoding='utf-8')
            (root/'index.html').write_text('old UI')
            (root/'ConnectConf.cfg').write_text('operator config')
            for directory in ('reports','downloads','cache','data'):
                (root/directory).mkdir()
                (root/directory/'preserve').write_text(directory)
            runtime.install_assets(root,bundle)
            self.assertEqual((root/'index.html').read_text(),'new index.html')
            self.assertEqual((root/'ConnectConf.cfg').read_text(),'operator config')
            for directory in ('reports','downloads','cache','data'):
                self.assertEqual((root/directory/'preserve').read_text(),directory)
            self.assertEqual(list(root.glob('.akuz-ui-*')),[])

    def test_first_launch_creates_only_example_config(self):
        with TemporaryDirectory() as scratch:
            root=Path(scratch)
            bundle=Path(runtime.__file__).resolve().parent
            runtime.install_assets(root,bundle)
            self.assertEqual((root/'ConnectConf.cfg').read_bytes(),
                             (bundle/'ConnectConf.example.cfg').read_bytes())
            self.assertFalse((root/'reports').exists())
