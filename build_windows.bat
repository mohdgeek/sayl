@echo off
REM Build Sayl.exe on Windows. Requires Python 3.10+ on PATH.
setlocal
cd /d "%~dp0"

python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err

pyinstaller --noconfirm --clean --onefile --console --name SaylHost run_host.py || goto :err
pyinstaller --noconfirm --onefile --windowed --name Sayl ^
  --icon assets\icon.ico --add-data "extension;extension" --add-data "dist\SaylHost.exe;host" ^
  --collect-all yt_dlp --collect-all Cryptodome run_sayl.py || goto :err

echo.
echo Done: dist\Sayl.exe
echo For best video quality and MP3 conversion, install ffmpeg:  winget install ffmpeg
pause
exit /b 0

:err
echo Build failed. Copy the error text above and send it for help.
pause
exit /b 1
