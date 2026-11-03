import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperstudio import paths


class PathTests(unittest.TestCase):
    def test_source_checkout_keeps_existing_data_location(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(paths, 'SOURCE_CHECKOUT', True):
            self.assertEqual(paths.default_data_dir(), paths.ROOT/'data')

    def test_explicit_data_directory_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PAPER_STUDIO_DATA_DIR':tmp}):
            self.assertEqual(paths.default_data_dir(), Path(tmp).resolve())

    def test_installed_package_uses_user_data_instead_of_site_packages(self):
        with patch.dict(os.environ, {'LOCALAPPDATA':'C:/ExampleLocal','XDG_DATA_HOME':'/example-data'}, clear=True),patch.object(paths,'SOURCE_CHECKOUT',False):
            with patch.object(paths.sys,'platform','win32'):
                self.assertEqual(paths.default_data_dir(),Path('C:/ExampleLocal/PaperStudio/data'))
            with patch.object(paths.sys,'platform','linux'):
                self.assertEqual(paths.default_data_dir(),Path('/example-data/paper-studio'))

    def test_all_runtime_assets_are_present(self):
        for name in ['index.html','styles.css','app.js','ai.js','standards.js','standards.css','creative.html','auditorium.jpg','sample.png']:
            self.assertTrue((paths.WEB/name).is_file(),name)
