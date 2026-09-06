@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 웹 화면 (폰/PC 공용, 닫지 마세요)
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 설치가 안 되어 있습니다. 0_처음설치.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
if not exist "web\data\forecast.json" (
  echo 웹용 자료가 없어 지금 만듭니다...
  ".venv\Scripts\python.exe" -u -m weather.export_web
)
".venv\Scripts\python.exe" -u -m weather.serve
pause
