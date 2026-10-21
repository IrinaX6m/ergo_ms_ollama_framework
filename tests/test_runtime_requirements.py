from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from modules.ollama_framework.deployment.ollama_models_loader import load_resolved_models
from runtime_requirements import RuntimeRequirementsError

ROOT = Path(__file__).resolve().parents[3]
LOADER = ROOT / 'modules/ollama_framework/deployment/ollama_models_loader.py'


class RuntimeModelRequirementsTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'virtual_env/cache/tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix='test_ollama_manifest_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / 'modules/ollama_framework'
        (self.module / 'api').mkdir(parents=True)
        self.manifest = self.module / 'runtime_requirements.yaml'
        self.manifest.write_text('schema_version: 1\nmodels:\n  - provider: ollama\n    name: fixture-model:1b\n    required: true\n', encoding='utf-8')
        env = patch.dict(os.environ, {'DISABLED_MODULES': '', 'MODULE_RUNTIME': 'monolith', 'ERGO_PROCESS_ROLE': '', 'PROCESS_MODULES': ''})
        env.start()
        self.addCleanup(env.stop)
        load_resolved_models.cache_clear()
        self.addCleanup(load_resolved_models.cache_clear)

    def test_manifest_without_legacy_hook_is_resolved(self):
        models = load_resolved_models(str(self.root))
        self.assertEqual([(model.name, model.required) for model in models], [('fixture-model:1b', True)])
        self.assertTrue(models[0].source_file.endswith('runtime_requirements.yaml'))

    def test_equal_legacy_hook_is_deduplicated(self):
        (self.module / 'ollama_models.yaml').write_text('models:\n  - name: fixture-model:1b\n    required: true\n', encoding='utf-8')
        self.assertEqual(len(load_resolved_models(str(self.root))), 1)

    def test_conflicting_legacy_hook_fails(self):
        (self.module / 'ollama_models.yaml').write_text('models:\n  - name: fixture-model:1b\n    required: false\n', encoding='utf-8')
        with self.assertRaises(RuntimeRequirementsError):
            load_resolved_models(str(self.root))

    def test_disabled_consumer_is_not_resolved(self):
        with patch.dict(os.environ, {'DISABLED_MODULES': 'ollama_framework'}):
            self.assertEqual(load_resolved_models(str(self.root)), ())

    def test_standalone_cli_resolves_manifest_without_pythonpath(self):
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, str(LOADER), '--root', str(self.root), '--json'], cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['models'][0]['name'], 'fixture-model:1b')

    def test_standalone_cli_reports_manifest_errors_without_traceback(self):
        self.manifest.write_text('schema_version: 100\n', encoding='utf-8')
        result = subprocess.run([sys.executable, str(LOADER), '--root', str(self.root), '--json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('schema_version', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_model_respects_process_environment(self):
        self.manifest.write_text('schema_version: 1\nmodels:\n  - provider: ollama\n    env: ERGO_TEST_RUNTIME_MODEL\n    default: fixture-default:1b\n', encoding='utf-8')
        (self.module / '.env').write_text('ERGO_TEST_RUNTIME_MODEL=fixture-file:1b\n', encoding='utf-8')
        with patch.dict(os.environ, {'ERGO_TEST_RUNTIME_MODEL': 'fixture-process:1b'}):
            self.assertEqual(load_resolved_models(str(self.root))[0].name, 'fixture-process:1b')

    def test_unresolved_required_model_fails(self):
        self.manifest.write_text('schema_version: 1\nmodels:\n  - provider: ollama\n    env: ERGO_TEST_RUNTIME_UNRESOLVED\n', encoding='utf-8')
        with patch.dict(os.environ, {'ERGO_TEST_RUNTIME_UNRESOLVED': ''}), self.assertRaises(RuntimeRequirementsError):
            load_resolved_models(str(self.root))

    def test_unresolved_optional_model_is_skipped(self):
        self.manifest.write_text('schema_version: 1\nmodels:\n  - provider: ollama\n    env: ERGO_TEST_RUNTIME_UNRESOLVED\n    required: false\n', encoding='utf-8')
        with patch.dict(os.environ, {'ERGO_TEST_RUNTIME_UNRESOLVED': ''}):
            self.assertEqual(load_resolved_models(str(self.root)), ())


if __name__ == '__main__':
    unittest.main()
