import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


class ReleaseTests(unittest.TestCase):
    def test_distribution_contains_runtime_code_but_no_personal_state(self):
        self.assertIsNotNone(importlib.util.find_spec('build_release'), '发行包工具尚未实现')
        from build_release import build
        with tempfile.TemporaryDirectory() as folder:
            output = build(ROOT, Path(folder))
            with zipfile.ZipFile(output['archive']) as package:
                files = package.namelist()
                self.assertIn('job-search-assistant/skills/job-search-assistant/SKILL.md', files)
                self.assertIn('job-search-assistant/skills/job-search-assistant/scripts/job_assistant/desktop.py', files)
                self.assertIn('job-search-assistant/install.ps1', files)
                self.assertIn('job-search-assistant/skills/job-search-assistant/assets/dashboard/app.js', files)
                self.assertIn('job-search-assistant/open-dashboard.ps1', files)
                self.assertFalse(any('.verification' in name or name.endswith('.sqlite3') or '__pycache__' in name for name in files))
            self.assertTrue(Path(output['checksum']).is_file())


if __name__ == '__main__':
    unittest.main()
