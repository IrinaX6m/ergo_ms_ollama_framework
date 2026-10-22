import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from modules.ollama_framework.deployment.model_store import inspect_model
from modules.ollama_framework.deployment.model_names import matches_installed
from modules.ollama_framework.deployment.ollama_models_loader import ResolvedOllamaModel
from modules.ollama_framework.deployment import pull_setup_models as pull

ROOT = Path(__file__).resolve().parents[3]


class LocalModelIndexTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'virtual_env/cache/tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.work = tempfile.TemporaryDirectory(dir=base, prefix='ollama_index_')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.store = self.root/'virtual_env/packages/ollama_models'

    def ready(self):
        blobs = self.store/'blobs'
        blobs.mkdir(parents=True)
        layers = []
        for content, kind in [(b'{"family":"test"}', 'application/vnd.docker.container.image.v1+json'), (b'GGUF'+b'x'*1024, 'application/vnd.ollama.image.model')]:
            digest = hashlib.sha256(content).hexdigest()
            (blobs/('sha256-'+digest)).write_bytes(content)
            layers.append({'digest':'sha256:'+digest, 'size':len(content), 'mediaType':kind})
        manifest = self.store/'manifests/registry.ollama.ai/library/tiny/latest'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'schemaVersion':2, 'config':layers[0], 'layers':layers[1:]}))
        return manifest, blobs/('sha256-'+layers[1]['digest'][7:])

    def test_complete_local_manifest_and_blobs_resolve_without_server(self):
        manifest, _ = self.ready()
        self.assertEqual(inspect_model(self.root, 'tiny', verify_hashes=True)['local_path'], str(manifest.resolve()))
        self.assertEqual(inspect_model(self.root, 'tiny')['runtime_name'], 'tiny:latest')

    def test_missing_or_truncated_blob_is_not_ready(self):
        _, blob = self.ready()
        blob.write_bytes(b'partial')
        self.assertFalse(inspect_model(self.root, 'tiny')['ready'])

    def test_full_integrity_check_rejects_same_size_corruption(self):
        _, blob = self.ready()
        blob.write_bytes(b'y' * blob.stat().st_size)
        self.assertFalse(inspect_model(self.root, 'tiny', verify_hashes=True)['ready'])

    def test_runner_manifest_list_resolves_its_local_child(self):
        manifest, _ = self.ready()
        content = manifest.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        (self.store/'blobs'/('sha256-'+digest)).write_bytes(content)
        manifest.write_text(json.dumps({'schemaVersion':2, 'mediaType':'application/vnd.ollama.manifest.list.v2+json', 'manifests':[{'runner':'ggml','digest':'sha256:'+digest}]}))
        self.assertTrue(inspect_model(self.root, 'tiny', verify_hashes=True)['ready'])
        (self.store/'blobs'/('sha256-'+digest)).unlink()
        self.assertFalse(inspect_model(self.root, 'tiny')['ready'])

    def test_unsafe_model_names_are_rejected(self):
        for name in ('../bad', 'tiny:../bad', 'C:\\escape', '/absolute', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_model(self.root, name)

    def test_different_tag_or_name_is_not_an_installed_alias(self):
        self.assertTrue(matches_installed('tiny', ['tiny:latest']))
        self.assertFalse(matches_installed('tiny:v2', ['tiny:v1']))
        self.assertFalse(matches_installed('tiny', ['other-tiny:latest']))

    def test_setup_installs_only_required_models(self):
        models = (ResolvedOllamaModel('required'), ResolvedOllamaModel('optional', required=False))
        ops = Mock()
        ops.pull_model.return_value = True
        with patch.object(pull, 'inspect_model', side_effect=[{'ready':False},{'runtime_name':'required:latest'},{'ready':False},{'ready':True}]), patch.object(pull, 'load_resolved_models', return_value=models), patch.object(pull, 'is_installed', return_value=True), patch.object(pull, 'OllamaOps', return_value=ops):
            self.assertEqual(pull.pull_setup_models(self.root), 0)
        ops.pull_model.assert_called_once_with('required')

    def test_missing_binary_does_not_report_required_models_ready(self):
        with patch.object(pull, 'is_installed', return_value=False):
            self.assertEqual(pull.pull_models(self.root, (ResolvedOllamaModel('required'),)), 1)

    def test_ready_model_does_not_start_a_server_or_pull(self):
        self.ready()
        with patch.object(pull, 'OllamaOps') as ops:
            self.assertEqual(pull.pull_models(self.root, (ResolvedOllamaModel('tiny'),)), 0)
        ops.assert_not_called()

    def test_sdk_success_without_local_artifacts_is_a_failure(self):
        ops = Mock()
        ops.pull_model.return_value = True
        with patch.object(pull, 'is_installed', return_value=True), patch.object(pull, 'OllamaOps', return_value=ops):
            self.assertEqual(pull.pull_models(self.root, (ResolvedOllamaModel('tiny'),)), 1)

    def test_sdk_uses_the_configured_server_and_proxy_policy(self):
        import types
        from modules.ollama_framework.deployment import ollama_ops
        client = Mock()
        with patch.dict('sys.modules', {'ollama':types.SimpleNamespace(Client=client)}), patch.object(ollama_ops,'get_ollama_base_url', return_value='http://127.0.0.1:22114'), patch.object(ollama_ops,'ergo_http_trust_env',return_value=False):
            ollama_ops.OllamaOps().get_ollama_client()
        client.assert_called_once_with(host='http://127.0.0.1:22114',trust_env=False)
