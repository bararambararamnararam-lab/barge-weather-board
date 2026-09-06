@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 엑셀 내보내기
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 설치가 안 되어 있습니다. 0_처음설치.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m weather.export
echo.
pause
