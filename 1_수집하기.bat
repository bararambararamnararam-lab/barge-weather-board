@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 데이터 수집
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 설치가 안 되어 있습니다. 0_처음설치.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
echo ============================================================
echo  예보와 기상특보를 지금 한 번 수집합니다. 1~2분 걸립니다.
echo ============================================================
echo.
".venv\Scripts\python.exe" -u -m weather.collect all
echo.
echo ============================================================
echo  끝났습니다. 2_화면열기.bat 으로 결과를 보세요.
echo ============================================================
pause
