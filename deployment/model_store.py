"""Read local Ollama manifests without contacting or starting a server."""

import hashlib
import json
from pathlib import Path
import re

from .paths import get_ollama_models_dir


def model_identity(name: str) -> tuple[str, str, str, str]:
    if not isinstance(name, str) or not name.strip() or '\\' in name:
        raise ValueError('Недопустимое имя модели Ollama')
    raw = name.strip()
    if ':' in raw.rsplit('/', 1)[-1]:
        raw, tag = raw.rsplit(':', 1)
    else:
        tag = 'latest'
    parts = raw.split('/')
    if len(parts) == 1:
        host, namespace, model = 'registry.ollama.ai', 'library', parts[0]
    elif len(parts) == 2:
        host, namespace, model = 'registry.ollama.ai', *parts
    elif len(parts) == 3:
        host, namespace, model = parts
    else:
        raise ValueError('Недопустимое имя модели Ollama')
    for index, part in enumerate((host, namespace, model, tag)):
        pattern = r'[A-Za-z0-9][A-Za-z0-9_.:-]*' if index == 0 else r'[A-Za-z0-9][A-Za-z0-9_.-]*'
        if not re.fullmatch(pattern, part) or '..' in part:
            raise ValueError('Недопустимое имя модели Ollama')
    return host.lower(), namespace.lower(), model.lower(), tag


def canonical_model_name(name: str) -> str:
    host, namespace, model, tag = model_identity(name)
    prefix = '' if host == 'registry.ollama.ai' else host + '/'
    if namespace != 'library' or prefix:
        prefix += namespace + '/'
    return f'{prefix}{model}:{tag}'


def _blob(store, descriptor, verify_hashes, *, require_size=True):
    if not isinstance(descriptor, dict):
        raise ValueError('Некорректный descriptor Ollama')
    digest, size = descriptor.get('digest'), descriptor.get('size')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Некорректный digest Ollama')
    path = store / 'blobs' / digest.replace(':', '-')
    if not path.resolve().is_relative_to(store) or not path.is_file():
        raise ValueError('Нет локального blob Ollama')
    if (require_size or size is not None) and (type(size) is not int or size < 0 or path.stat().st_size != size):
        raise ValueError('Неполный blob Ollama')
    if verify_hashes:
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest[7:]:
                raise ValueError('Digest blob Ollama не совпадает')
    return path


def _read_json(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Слишком большой manifest Ollama')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Некорректный manifest Ollama')
    return data


def _manifest_ready(store, data, verify_hashes, *, depth=0):
    if depth > 4 or not isinstance(data, dict):
        return False
    # New Ollama releases may keep runner-specific child manifests by digest.
    if data.get('mediaType') == 'application/vnd.ollama.manifest.list.v2+json':
        children = data.get('manifests')
        if not isinstance(children, list):
            return False
        for child in children:
            if isinstance(child, dict) and child.get('runner', '') in ('', 'ggml', 'llamacpp'):
                try:
                    if _manifest_ready(store, child, verify_hashes, depth=depth+1):
                        return True
                except (OSError, ValueError, TypeError):
                    continue  # Another locally installed runner may be complete.
        return False
    if data.get('digest') and not data.get('config') and not data.get('layers'):
        return _manifest_ready(store, _read_json(_blob(store, data, verify_hashes, require_size=False)), verify_hashes, depth=depth+1)
    if data.get('schemaVersion') != 2 or not isinstance(data.get('layers'), list):
        return False
    config = _blob(store, data.get('config'), verify_hashes)
    _read_json(config)
    weights = False
    for layer in data['layers']:
        blob = _blob(store, layer, verify_hashes)
        if layer.get('mediaType') == 'application/vnd.ollama.image.model' and blob.stat().st_size > 0:
            weights = True
    return weights


def inspect_model(root: Path, name: str, *, verify_hashes=False) -> dict:
    host, namespace, model, tag = model_identity(name)
    store = get_ollama_models_dir(root).resolve()
    manifest = store / 'manifests' / host / namespace / model / tag
    result = {'name': canonical_model_name(name), 'runtime_name': canonical_model_name(name),
              'required': True, 'ready': False, 'local_path': None, 'reason': 'model_missing'}
    try:
        if not manifest.resolve().is_relative_to(store) or not manifest.is_file():
            return result
        result['reason'] = 'model_incomplete'
        if not _manifest_ready(store, _read_json(manifest), verify_hashes):
            return result
        result.update(ready=True, local_path=str(manifest.resolve()), reason='')
    except (OSError, ValueError, TypeError):
        return result
    return result
