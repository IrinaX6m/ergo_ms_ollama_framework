"""Execute installer functions with fake NSSM/SCM; never touch Windows services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1]
INSTALLER = Path(os.environ.get('OLLAMA_SERVICE_INSTALLER_TEST_SOURCE',
                               MODULE / 'deployment/windows/install-ollama-service.ps1'))


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell is required')
class WindowsServiceInstallerTests(unittest.TestCase):
    def run_scenario(self, name):
        powershell = shutil.which('powershell.exe')
        if powershell is None:
            self.skipTest('Windows PowerShell is unavailable')
        with tempfile.TemporaryDirectory(prefix='ollama_service_') as raw:
            root = Path(raw) / 'project with spaces & данные'
            root.mkdir()
            result = subprocess.run(
                [powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 str(MODULE / 'tests/windows_service_harness.ps1'), '-Installer', str(INSTALLER),
                 '-Root', str(root), '-Scenario', name],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip())

    def assert_success(self, result):
        self.assertEqual(result['Error'], '')
        self.assertTrue(result['Started'])
        self.assertIn('show-status', result['Calls'])
        self.assertIn('install-nssm|' + result['Root'], result['Calls'])

    def assert_refused(self, result, message):
        self.assertTrue(result['Error'])
        self.assertIn(message, result['Error'])
        self.assertIn('install-nssm|' + result['Root'], result['Calls'])
        self.assertFalse(result['Started'])
        self.assertNotIn('show-status', result['Calls'])
        self.assertFalse(any('installed and started' in text for text in result['Messages']))

    def test_first_install_passes_root_and_sets_service_paths(self):
        result = self.run_scenario('absent')
        self.assert_success(result)
        self.assertIn('set|ergo-ollama|AppDirectory|' + str(Path(result['Root']) / 'core'), result['Calls'])
        log = str(Path(result['Root']) / 'logs/ollama-serve.log')
        for stream in ('AppStdout', 'AppStderr'):
            self.assertIn(f'set|ergo-ollama|{stream}|{log}', result['Calls'])

    def test_wrapper_keeps_unicode_root_and_quotes_environment_assignment(self):
        result = self.run_scenario('absent')
        self.assert_success(result)
        self.assertIn(result['Root'], result['Wrapper'])
        self.assertIn(f'set "PYTHONPATH={result["Root"]}"', result['Wrapper'])
        self.assertFalse(result['WrapperHasBom'])

    def test_reinstall_waits_for_running_service_to_stop_and_disappear(self):
        result = self.run_scenario('running')
        self.assert_success(result)
        calls = result['Calls']
        self.assertLess(calls.index('stop|ergo-ollama'), calls.index('remove|ergo-ollama|confirm'))
        self.assertGreaterEqual(result['Sleeps'], 10)

    def test_stopped_service_is_not_stopped_again(self):
        result = self.run_scenario('stopped')
        self.assert_success(result)
        self.assertNotIn('stop|ergo-ollama', result['Calls'])

    def test_stop_pending_service_is_awaited_without_another_stop(self):
        result = self.run_scenario('stop-pending')
        self.assert_success(result)
        self.assertNotIn('stop|ergo-ollama', result['Calls'])

    def test_another_project_service_is_not_stopped_or_removed(self):
        result = self.run_scenario('other-root')
        self.assert_refused(result, 'another project')
        self.assertFalse(any(call.startswith(('stop|', 'remove|', 'install|', 'set|')) for call in result['Calls']))

    def test_missing_cli_is_detected_before_stopping_the_existing_service(self):
        result = self.run_scenario('missing-cli')
        self.assert_refused(result, 'ergoms')
        self.assertFalse(any(call.startswith(('stop|', 'remove|', 'install|')) for call in result['Calls']))

    def test_stop_timeout_does_not_remove_the_service(self):
        result = self.run_scenario('stop-timeout')
        self.assert_refused(result, 'did not stop')
        self.assertIn('stop|ergo-ollama', result['Calls'])
        self.assertNotIn('remove|ergo-ollama|confirm', result['Calls'])

    def test_remove_timeout_does_not_install_a_replacement(self):
        result = self.run_scenario('remove-timeout')
        self.assert_refused(result, 'was not removed')
        self.assertIn('remove|ergo-ollama|confirm', result['Calls'])
        self.assertFalse(any(call.startswith('install|') for call in result['Calls']))

    def test_failed_stop_does_not_remove_the_service(self):
        result = self.run_scenario('stop-error')
        self.assert_refused(result, 'NSSM stop failed')
        self.assertIn('stop|ergo-ollama', result['Calls'])
        self.assertNotIn('remove|ergo-ollama|confirm', result['Calls'])

    def test_failed_removal_does_not_install_a_replacement(self):
        result = self.run_scenario('remove-error')
        self.assert_refused(result, 'NSSM remove failed')
        self.assertFalse(any(call.startswith('install|') for call in result['Calls']))

    def test_failed_installation_does_not_configure_or_start_the_service(self):
        result = self.run_scenario('install-error')
        self.assert_refused(result, 'NSSM install failed')
        self.assertFalse(any(call.startswith('set|') for call in result['Calls']))

    def test_failed_configuration_does_not_start_the_service(self):
        self.assert_refused(self.run_scenario('config-error'), 'NSSM set failed')

    def test_failed_start_does_not_report_success(self):
        self.assert_refused(self.run_scenario('start-error'), 'Simulated start failure')
