@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 화면
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [오류] 설치가 안 되어 있습니다. 0_처음설치.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
echo ============================================================
echo  화면을 띄웁니다. 잠시 뒤 웹 브라우저가 저절로 열립니다.
echo.
echo  브라우저가 안 열리면 주소창에 직접 입력하세요:
echo      http://localhost:8501
echo.
echo  화면을 닫으려면 이 검은 창을 닫으세요.
echo ============================================================
echo.
".venv\Scripts\streamlit.exe" run "src\weather\app.py" --server.port 8501 --browser.gatherUsageStats false
pause
