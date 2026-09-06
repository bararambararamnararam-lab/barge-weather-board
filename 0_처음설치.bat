@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기상 조회 - 처음 설치
echo ============================================================
echo  한.중 바지선 운항 기상 조회 - 처음 설치
echo ============================================================
echo.
echo 파이썬이 깔려 있는지 확인합니다...
python --version
if errorlevel 1 (
  echo.
  echo [오류] 파이썬을 찾지 못했습니다.
  echo   https://www.python.org/downloads/ 에서 파이썬을 설치한 뒤
  echo   설치 화면에서 "Add python.exe to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)
echo.
echo 전용 실행 환경(.venv)을 만듭니다. 이미 있으면 그대로 씁니다...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
echo.
echo 필요한 프로그램들을 내려받아 설치합니다. 몇 분 걸릴 수 있습니다...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [오류] 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.
  pause
  exit /b 1
)
echo.
echo 기상청 API 키 파일을 확인합니다...
if not exist ".env" (
  if exist "..\.env" (
    copy /y "..\.env" ".env" >nul
    echo   상위 폴더의 .env 파일을 복사했습니다.
  ) else (
    copy /y ".env.example" ".env" >nul
    echo   [확인 필요] .env 파일을 만들었습니다.
    echo   메모장으로 .env 를 열어 기상청 API 키를 넣어 주세요.
  )
) else (
  echo   .env 파일이 이미 있습니다.
)
echo.
echo ============================================================
echo  설치가 끝났습니다.
echo  이제 1_수집하기.bat 을 실행해서 데이터를 한 번 모아 주세요.
echo ============================================================
pause
