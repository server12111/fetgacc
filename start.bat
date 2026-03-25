@echo off
echo ===============================
echo   Telegram Bot starting...
echo ===============================

REM Перехід у папку з цим файлом
cd /d %~dp0

REM Перевірка Python
python --version
if errorlevel 1 (
    echo Python не знайдено!
    pause
    exit /b
)

REM Встановлення залежностей (якщо ще не встановлені)
pip install -r requirements.txt

REM Запуск бота
python main.py

pause
