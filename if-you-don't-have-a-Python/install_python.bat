@echo off
chcp 65001 >nul
echo ================================
echo   Автоматическая установка Python
echo ================================
echo.

:: Проверка прав администратора
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [OK] Запущено с правами администратора
) else (
    echo [ОШИБКА] Требуются права администратора!
    echo Запустите скрипт от имени администратора
    pause
    exit /b 1
)

:: Переменные
set PYTHON_VERSION=3.12.3
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-amd64.exe
set INSTALLER_PATH=%TEMP%\python_installer.exe

echo Загрузка Python %PYTHON_VERSION%...
echo URL: %PYTHON_URL%

:: Загрузка установщика
powershell -Command "& {Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%INSTALLER_PATH%'}"
if %errorLevel% neq 0 (
    echo [ОШИБКА] Не удалось загрузить установщик Python
    pause
    exit /b 1
)

echo [OK] Установщик загружен

:: Автоматическая установка с добавлением в PATH
echo Установка Python...
"%INSTALLER_PATH%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0

if %errorLevel% neq 0 (
    echo [ОШИБКА] Установка завершилась с ошибкой
    goto cleanup
)

echo [OK] Python установлен

:: Обновление переменной PATH в текущей сессии
echo Обновление переменных окружения...
call :RefreshPath

:: Проверка установки
echo Проверка установки...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Python не найден в PATH
    echo Попробуйте перезапустить командную строку или компьютер
) else (
    echo [OK] Python успешно установлен и добавлен в PATH
    python --version
)

:: Установка pip (если нужно)
echo Проверка pip...
pip --version >nul 2>&1
if %errorLevel% neq 0 (
    echo Установка pip...
    python -m ensurepip --upgrade
) else (
    echo [OK] pip уже установлен
    pip --version
)

:: Обновление pip
echo Обновление pip...
python -m pip install --upgrade pip

:cleanup
echo Очистка временных файлов...
if exist "%INSTALLER_PATH%" del "%INSTALLER_PATH%"

echo.
echo ================================
echo   Установка завершена!
echo ================================
echo.
echo Рекомендации:
echo 1. Перезапустите командную строку для обновления PATH
echo 2. Проверьте установку командой: python --version
echo 3. Проверьте pip командой: pip --version
echo.
pause
exit /b 0

:: Функция обновления PATH
:RefreshPath
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "SysPath=%%b"
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "UserPath=%%b"
if defined UserPath (
    set "PATH=%UserPath%;%SysPath%"
) else (
    set "PATH=%SysPath%"
)
goto :eof