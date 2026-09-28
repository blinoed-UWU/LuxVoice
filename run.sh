#!/usr/bin/env bash
# Запуск LuxVoice из каталога проекта.
# Использование: ./run.sh [--diagnose | --headless | --command "фраза"]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Не найдено окружение .venv — выполните сначала ./install.sh" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON" -m luxvoice.main "$@"
