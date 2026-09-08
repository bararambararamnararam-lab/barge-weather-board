@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  이용_설명서.html 을 사이트용 자리로 복사합니다.
echo.

if not exist "이용_설명서.html" (
  echo  [오류] 이용_설명서.html 이 없습니다.
  echo         이 파일이 있는 폴더에서 실행해야 합니다.
  pause
  exit /b 1
)

if not exist "web\docs" mkdir "web\docs"
copy /y "이용_설명서.html" "web\docs\manual.html" >nul

if errorlevel 1 (
  echo  [오류] 복사에 실패했습니다.
  pause
  exit /b 1
)

echo  복사 완료: web\docs\manual.html
echo.
echo  ------------------------------------------------------------
echo   아직 인터넷에는 안 올라갔습니다.
echo   깃허브에 올려야 사이트에 나옵니다.
echo.
echo     git add -A
echo     git commit -m "설명서 수정"
echo     git push
echo.
echo   올리고 1분쯤 지나면 아래 주소에서 바뀐 내용이 보입니다.
echo   https://bararambararamnararam-lab.github.io/barge-weather-board/docs/manual.html
echo  ------------------------------------------------------------
echo.
pause
