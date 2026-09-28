@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   Установка LuxVoice для Windows
echo ========================================
echo.

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: Python не найден!
    echo.
    echo Скачайте Python 3.10+ с https://python.org
    echo При установке ОБЯЗАТЕЛЬНО отметьте "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo [1/5] Создание виртуального окружения...
python -m venv .venv
if errorlevel 1 (
    echo ОШИБКА: Не удалось создать виртуальное окружение
    pause
    exit /b 1
)

echo [2/5] Активация окружения...
call .venv\Scripts\activate.bat

echo [3/5] Обновление pip...
python -m pip install --upgrade pip -q

echo [4/5] Установка зависимостей...
pip install -r requirements-windows.txt -q
if errorlevel 1 (
    echo.
    echo ВНИМАНИЕ: Некоторые зависимости не установились
    echo Попробуйте установить вручную:
    echo   pip install -r requirements-windows.txt
    echo.
)

echo [5/5] Проверка установки...
python -c "import PyQt6; print('PyQt6: OK')"
python -c "import vosk; print('Vosk: OK')"
python -c "import pyautogui; print('PyAutoGUI: OK')"
python -c "import pycaw; print('PyCaw: OK')"

echo.
echo ========================================
echo   Установка завершена!
echo ========================================
echo.
echo Запуск:
echo   .\run.bat
echo.
echo Или с параметрами:
echo   .\run.bat --command "привет"
echo   .\run.bat --diagnose
echo.
echo Если возникли проблемы:
echo   .\run.bat --diagnose
echo.
pause
