@echo off
echo ===============================================
echo    GIF Studio Pro - Автоматическая установка
echo ===============================================
echo.

echo Установка Python зависимостей...
echo.

echo Устанавливаем основные зависимости...
pip install yt-dlp Pillow requests

echo.
echo Устанавливаем дополнительные зависимости для Windows...
pip install wmi

echo.
echo Устанавливаем FFmpeg через winget...
winget install FFmpeg

echo.
echo ===============================================
echo           Установка завершена!
echo ===============================================
echo.

echo Проверяем установку FFmpeg...
ffmpeg -version

echo.
echo Нажмите любую клавишу для выхода...
pause nul