@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 웹 자료 다시 만들기
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 설치가 안 되어 있습니다. 0_처음설치.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
echo 데이터베이스에 있는 최신 자료로 web\data\*.json 을 다시 만듭니다.
echo (보통은 1_수집하기.bat 이 자동으로 해 줍니다.)
echo.
".venv\Scripts\python.exe" -u -m weather.export_web
echo.
pause
