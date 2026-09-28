@echo off
chcp 65001 >nul

REM Активация виртуального окружения
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo ОШИБКА: Виртуальное окружение не найдено
    echo Запустите install.bat сначала
    pause
    exit /b 1
)

REM Запуск LuxVoice
python -m luxvoice.main %*
