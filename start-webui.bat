@echo off
rem Seedance WebUI launcher
cd /d D:\seedance-webui
rem Open browser after 4 seconds
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8100"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8100
echo.
echo Service stopped. Press any key to close.
pause >nul
