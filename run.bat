@echo off
REM Build the frontend (if needed) and run the OMR server on http://127.0.0.1:8000
SETLOCAL
SET PY=C:\ProgramData\anaconda3\envs\all\python.exe

IF NOT EXIST "%~dp0frontend\dist\index.html" (
  echo Building frontend...
  pushd "%~dp0frontend"
  call npm install --legacy-peer-deps
  call npm run build
  popd
)

echo Starting OMR server at http://127.0.0.1:8000
cd /d "%~dp0backend"
"%PY%" -m uvicorn app:app --host 127.0.0.1 --port 8000
ENDLOCAL
