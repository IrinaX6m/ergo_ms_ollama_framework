"""Локальная обработка не передаёт сообщения другому серверу или облачной модели."""

import unittest
from unittest.mock import Mock, patch

import httpx

from modules.ollama_framework.api.client.base import LLMClientError
from modules.ollama_framework.api.client.factory import build_llm_client
from modules.ollama_framework.api.client.httpx_client import HttpxOllamaClient


class LocalChatTests(unittest.TestCase):
    def client(self, **kwargs):
        client = HttpxOllamaClient(model='test:local', base_url=kwargs.pop('base_url', 'http://127.0.0.1:11434'),
                                  request_timeout=3, stream_timeout=3, concurrency_limit=1, max_retries=0,
                                  keep_alive='0', local_only=True, **kwargs)
        self.addCleanup(client._client.close)
        return client

    def test_remote_hosts_credentials_paths_and_names_are_rejected(self):
        for address in ('http://example.com:11434', 'http://localhost:11434', 'http://127.0.0.1.evil.test',
                        'http://user:pass@127.0.0.1:11434', 'http://127.0.0.1:11434/proxy'):
            with self.subTest(address=address), self.assertRaises(LLMClientError):
                self.client(base_url=address)

    def test_local_transport_ignores_environment_proxy_and_uses_short_read_timeout(self):
        with patch('src.core.utils.http_proxy.http_trust_env', return_value=True), patch('httpx.Client') as factory:
            self.client()
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertEqual(factory.call_args.kwargs['timeout'].read, 3)

    def test_cloud_or_unknown_model_metadata_prevents_chat(self):
        for info in ({}, {'model_info': {}}, {'model_info': {'general.architecture': 'x'}, 'remote_host': 'cloud.test'},
                     {'model_info': {'general.architecture': 'x'}, 'remote_model': 'cloud'}):
            with self.subTest(info=info):
                client = self.client()
                client._client.close()
                client._client = Mock()
                client._client.post.return_value.json.return_value = info
                with self.assertRaises(LLMClientError):
                    client.chat([{'role': 'user', 'content': 'private', 'images': ['private-image']}])
                self.assertEqual(client._client.post.call_count, 1)
                self.assertEqual(client._client.post.call_args.args, ('/api/show',))

    def test_missing_model_chat_does_not_fall_back_to_another_address(self):
        client = self.client()
        client._client.close()
        client._client = Mock()
        client._client.post.side_effect = [
            httpx.Response(200, json={'model_info': {'general.architecture': 'test'}}, request=httpx.Request('POST', 'http://127.0.0.1:11434/api/show')),
            httpx.Response(404, json={'error': 'model not found'}, request=httpx.Request('POST', 'http://127.0.0.1:11434/api/chat')),
        ]
        with patch('modules.ollama_framework.api.settings.OLLAMA_FILE_BASE_URL', 'https://external.test'):
            with self.assertRaises(LLMClientError):
                client.chat([{'role': 'user', 'content': 'private'}])
        self.assertEqual([call.args[0] for call in client._client.post.call_args_list], ['/api/show', '/api/chat'])

    def test_strict_and_ordinary_clients_do_not_share_cache(self):
        params = dict(provider='ollama', model='local-chat-cache-test', base_url='http://127.0.0.1:11434',
                      request_timeout=3, stream_timeout=3, concurrency_limit=1, max_retries=0)
        ordinary = build_llm_client(**params)
        strict = build_llm_client(**params, provider_config={'local_only': True})
        self.addCleanup(ordinary._client.close)
        self.addCleanup(strict._client.close)
        self.assertIsNot(ordinary, strict)

    def test_local_structured_chat_preserves_disabled_thinking(self):
        client = self.client(device_config={'think': False})
        client._client.close()
        client._client = Mock()
        client._client.post.side_effect = [
            httpx.Response(200, json={'model_info': {'general.architecture': 'test'}}, request=httpx.Request('POST', 'http://127.0.0.1/api/show')),
            httpx.Response(200, json={'message': {'content': '{"answer":"12345"}'}}, request=httpx.Request('POST', 'http://127.0.0.1/api/chat')),
        ]
        answer = client.chat([{'role': 'user', 'content': 'read', 'images': ['pixels']}], format={'type': 'object'})
        self.assertEqual(answer, '{"answer":"12345"}')
        self.assertIs(client._client.post.call_args.kwargs['json']['think'], False)
