"""JSON adapter for the shared index; downloads remain in the existing ops."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'core/deployment'))

from modules.ollama_framework.deployment.model_store import inspect_model  # noqa: E402
from modules.ollama_framework.deployment.ollama_models_loader import load_resolved_models, ResolvedOllamaModel  # noqa: E402
from modules.ollama_framework.deployment.pull_setup_models import pull_models  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['list', 'resolve', 'install'])
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--model', default='')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.action == 'list':
            models = []
            for model in load_resolved_models(str(root)):
                models.append({**inspect_model(root, model.name), 'required':model.required,
                               'source_module':model.source_module, 'source_file':model.source_file})
            result = {'models':models}
        else:
            if args.action == 'install':
                if root != PROJECT_ROOT.resolve():
                    raise ValueError('Установка моделей требует кода провайдера из выбранного корня')
                with redirect_stdout(sys.stderr):
                    code = pull_models(root, (ResolvedOllamaModel(name=args.model),), force=args.force)
                if code:
                    return code
            result = inspect_model(root, args.model)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
