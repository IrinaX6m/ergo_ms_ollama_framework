"""
Скачивание моделей Ollama, объявленных в modules/*/ollama_models.yaml.

Вызов: ergoms ollama_framework:pull-setup-models
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from model_index.locking import model_lock  # noqa: E402

from modules.ollama_framework.deployment.install_ollama import is_installed  # noqa: E402
from modules.ollama_framework.deployment.model_names import (  # noqa: E402
    is_ollama_library_name,
)
from modules.ollama_framework.deployment.ollama_models_loader import (  # noqa: E402
    load_resolved_models,
)
from modules.ollama_framework.deployment.ollama_ops import OllamaOps  # noqa: E402
from modules.ollama_framework.deployment.model_store import inspect_model  # noqa: E402


def pull_setup_models(root: Path, *, include_optional: bool = False) -> int:
    root = root.resolve()
    models = [model for model in load_resolved_models(str(root)) if model.required or include_optional]
    return pull_models(root, models)


def pull_models(root: Path, models, *, force: bool = False) -> int:
    root = root.resolve()
    models = [model for model in models if force or not inspect_model(root, model.name)['ready']]
    if not models:
        print(format_console('skip', 'Нет моделей для установки (ollama_models.yaml)'))
        return 0

    if not is_installed(root):
        print(format_console('error', 'Ollama не установлен; требуемые модели недоступны'))
        return 1

    ops = None
    failed_required: list[str] = []
    for model in models:
        if not is_ollama_library_name(model.name):
            print(
                format_console(
                    'skip',
                    f'{model.name} — снимок Hugging Face, не Ollama '
                    f'(huggingface_models.yaml / ergoms pull-huggingface-models)',
                )
            )
            continue
        runtime_name = inspect_model(root, model.name)['runtime_name']
        with model_lock(root, 'ollama', runtime_name):
            if not force and inspect_model(root, model.name)['ready']:
                continue
            if ops is None:
                ops = OllamaOps()
                try:
                    ops.ensure_ollama_running()
                except RuntimeError as exc:
                    print(format_console('error', str(exc)))
                    return 1
            source_hint = model.source_module or 'ollama_framework'
            print(format_console('info', f'Скачиваю {model.name} ({source_hint})…'))
            if ops.pull_model(model.name) and inspect_model(root, model.name)['ready']:
                continue
        if model.required:
            failed_required.append(model.name)
        else:
            print(format_console('warning', f'Не удалось скачать необязательную модель {model.name}'))

    if failed_required:
        names = ', '.join(failed_required)
        print(format_console('error', f'Не удалось скачать обязательные модели: {names}'))
        print(format_console('info', 'Повторите: ergoms ollama_framework:pull-setup-models'))
        return 1

    print(format_console('ok', 'Модели setup-full готовы'))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        prog='ergoms ollama_framework:pull-setup-models',
        description='Скачать модели Ollama из modules/*/ollama_models.yaml',
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=PROJECT_ROOT,
        help='Корень проекта ERGO MS',
    )
    parser.add_argument('--include-optional', action='store_true', help='Также скачать необязательные модели')
    args = parser.parse_args(argv)
    try:
        return pull_setup_models(args.root.resolve(), include_optional=args.include_optional)
    except Exception as exc:
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
