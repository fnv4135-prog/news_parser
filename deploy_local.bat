@echo off
echo.
echo ========================================
echo   News Parser - Deploy
echo ========================================
echo.

set /p MSG="Commit message (Enter for 'update'): "
if "%MSG%"=="" set MSG=update

echo.
echo [1/3] Push to GitHub...
git add .
git commit -m "%MSG%" 2>nul
git push origin main
if %errorlevel% neq 0 (
    echo ERROR: git push failed
    pause
    exit /b 1
)

echo.
echo [2/3] Deploy to server...
ssh root@90.156.255.34 "cd /root/bots/news_parser && git stash && git pull origin main && git stash pop && ./deploy.sh '%MSG%'"
if %errorlevel% neq 0 (
    echo ERROR: deploy failed
    pause
    exit /b 1
)

echo.
echo [3/3] Done!
echo ========================================
pause