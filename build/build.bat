@echo off
REM ============================================================
REM PromptSprite one-click build script (pure ASCII, no encoding issues)
REM Prereq: .venv exists with requirements.txt and pyinstaller installed
REM Usage: build\build.bat   (double-click or run in terminal)
REM ============================================================
cd /d "%~dp0.."

echo [1/3] Generate app icon (build\app.ico) ...
.venv\Scripts\python.exe build\generate_icon.py

echo [2/3] Run PyInstaller (onefile, windowed) ...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean build\PromptSprite.spec
if errorlevel 1 (
    echo Build FAILED. Check the error messages above.
    pause
    exit /b 1
)

echo [3/3] Build done. Artifact:
dir dist\PromptSprite.exe
echo.
echo Hint: on first run, a data\ folder is created next to the exe
echo       and the builtin manual is imported automatically.
echo Hint: the global hotkey needs admin rights or an antivirus
echo       whitelist entry; failure does not block other features.
pause
