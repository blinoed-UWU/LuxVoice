#!/usr/bin/env bash
# Установка LuxVoice: окружение, зависимости, модели, интеграция.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()   { echo "${GREEN}✓${RESET} $*"; }
warn() { echo "${YELLOW}!${RESET} $*"; }
fail() { echo "${RED}✗${RESET} $*"; }
head() { echo; echo "${BOLD}$*${RESET}"; }

echo "${BOLD}Установка LuxVoice — голосового ассистента${RESET}"
echo "Каталог: $PROJECT_DIR"

# --- Проверка Python ---
head "1. Проверка Python"
PYTHON_BIN=""
for candidate in python3.14 python3.13 python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        major=${version%%.*}; minor=${version##*.}
        if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
            PYTHON_BIN="$candidate"; break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    fail "Не найден Python 3.10 или новее."
    echo "  Установите: sudo pacman -S python"
    exit 1
fi
ok "Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# --- Виртуальное окружение ---
head "2. Виртуальное окружение"
if [[ -d .venv ]]; then
    ok "Окружение уже существует"
else
    "$PYTHON_BIN" -m venv --system-site-packages .venv || {
        fail "Не удалось создать окружение"; exit 1; }
    ok "Окружение создано"
fi

VENV_PY=".venv/bin/python"
"$VENV_PY" -m pip install --upgrade pip wheel setuptools --quiet 2>/dev/null
ok "pip обновлён"

# --- Зависимости ---
head "3. Библиотеки Python"
CORE="numpy requests pyyaml psutil sounddevice"
OPTIONAL="vosk faster-whisper piper-tts webrtcvad"

echo "  Базовые библиотеки…"
"$VENV_PY" -m pip install --quiet $CORE && ok "Базовые установлены" || \
    warn "Часть базовых библиотек не установилась"

for package in $OPTIONAL; do
    printf "  %-16s " "$package"
    if "$VENV_PY" -c "import importlib,sys; importlib.import_module('${package//-/_}'.split('_')[0])" 2>/dev/null; then
        echo "уже есть"
    elif "$VENV_PY" -m pip install --quiet "$package" 2>/dev/null; then
        echo "установлен"
    else
        echo "${YELLOW}не установился${RESET}"
    fi
done

# --- Системные пакеты ---
head "4. Системные пакеты"
MISSING=()
for pkg in pipewire libnotify ffmpeg; do
    if pacman -Qq "$pkg" >/dev/null 2>&1; then ok "$pkg"; else MISSING+=("$pkg"); fi
done

if ! command -v wpctl >/dev/null 2>&1; then MISSING+=("wireplumber"); fi

# RHVoice и espeak — синтез речи
for pkg in rhvoice rhvoice-language-russian espeak-ng; do
    if pacman -Qq "$pkg" >/dev/null 2>&1; then
        ok "$pkg"
    else
        printf "  %-26s " "$pkg"
        if sudo -n pacman -S --noconfirm --needed "$pkg" >/dev/null 2>&1; then
            echo "установлен"
        else
            echo "${YELLOW}требуется пароль (установите вручную)${RESET}"
        fi
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Не установлены: ${MISSING[*]}"
    echo "  Установите: sudo pacman -S ${MISSING[*]}"
fi

# --- Модель распознавания ---
head "5. Модель распознавания речи"
if "$VENV_PY" -c "
import sys; sys.path.insert(0, '.')
from luxvoice.stt.engines import vosk_model_installed
sys.exit(0 if vosk_model_installed('ru') else 1)
" 2>/dev/null; then
    ok "Русская модель уже установлена"
else
    echo "  Загрузка русской модели (около 45 МБ)…"
    if "$VENV_PY" -m luxvoice.main --install-model ru; then
        ok "Модель установлена"
    else
        warn "Модель не загрузилась — загрузите её позже:"
        echo "  ./run.sh --install-model ru"
    fi
fi

# --- Голоса синтеза ---
head "6. Голоса синтеза речи"
VOICES_DIR="$HOME/.local/share/luxvoice/voices"
if [[ -d "$VOICES_DIR" ]] && compgen -G "$VOICES_DIR/*.onnx" >/dev/null; then
    count=$(ls "$VOICES_DIR"/*.onnx 2>/dev/null | wc -l)
    ok "Голосов найдено: $count"
else
    echo "  Голоса не найдены. Загружаю русские голоса с зеркала (около 55 МБ)…"
    mkdir -p "$VOICES_DIR"
    BASE="https://github.com/Rotem12/piper-russian-voices/releases/download/russian-voices-2026-09-15-r2"
    for voice in ru_RU-irina-medium ru_RU-dmitri-medium; do
        printf "  %-22s " "$voice"
        if curl -sL --retry 3 --retry-all-errors -o "/tmp/$voice.zip" "$BASE/$voice.zip" 2>/dev/null \
           && unzip -oq "/tmp/$voice.zip" -d "$VOICES_DIR" 2>/dev/null; then
            echo "установлен"
            rm -f "/tmp/$voice.zip"
        else
            echo "${YELLOW}не удалось${RESET}"
        fi
    done
    if compgen -G "$VOICES_DIR/*.onnx" >/dev/null; then
        ok "Голоса установлены"
    else
        warn "Голоса недоступны — можно установить RHVoice: sudo pacman -S rhvoice rhvoice-language-russian"
    fi
fi

# --- Доступ к управлению компьютером ---
head "7. Доступ к управлению компьютером"
if "$VENV_PY" -c "
import sys; sys.path.insert(0, '.')
from luxvoice.sysint.uinput import UInputDevice
sys.exit(0 if UInputDevice.available().uinput else 1)
" 2>/dev/null; then
    ok "Доступ к виртуальному вводу есть"
else
    warn "Нет доступа к /dev/uinput — команды с клавишами и мышью не будут работать"
    if groups | grep -qw input; then
        echo "  Вы уже в группе input. Нужно выйти из системы и войти заново."
    else
        printf "  Добавить вас в группу input? [y/N] "
        read -r answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            if sudo usermod -aG input "$USER"; then
                ok "Вы добавлены в группу input"
                echo "  ${YELLOW}Выйдите из системы и войдите заново${RESET}"
            else
                fail "Не удалось. Выполните вручную:"
                echo "    sudo usermod -aG input \$USER"
            fi
        fi
    fi
fi

# --- Интеграция с системой ---
head "8. Ярлык и автозапуск"
"$VENV_PY" -m luxvoice.sysint.integration --install >/dev/null 2>&1 && \
    ok "Ярлык добавлен в меню приложений" || \
    warn "Не удалось создать ярлык"

printf "  Включить автозапуск при входе в систему? [y/N] "
read -r answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    "$VENV_PY" -m luxvoice.sysint.integration --autostart on >/dev/null 2>&1 && \
        ok "Автозапуск включён" || warn "Не удалось включить автозапуск"
else
    echo "  Автозапуск оставлен выключенным"
fi

# --- Проверка ---
head "9. Проверка готовности"
"$VENV_PY" -m luxvoice.main --diagnose || true

head "Готово"
echo "Запуск программы:"
echo "  ${BOLD}./run.sh${RESET}                 — графический интерфейс"
echo "  ${BOLD}./run.sh --headless${RESET}      — без интерфейса"
echo "  ${BOLD}./run.sh --diagnose${RESET}      — проверка компонентов"
echo "  ${BOLD}./run.sh --command \"фраза\"${RESET} — выполнить одну команду"
echo
echo "Или через меню приложений: значок «LuxVoice»"
