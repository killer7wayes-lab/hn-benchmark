@echo off
title Publish hn-benchmark to GitHub
cd /d "%~dp0"
set REPO=https://github.com/killer7wayes-lab/hn-benchmark.git

git --version >nul 2>&1
if errorlevel 1 ( echo Git is not installed. Install it from https://git-scm.com then run this again. & pause & exit /b )

if exist site\data.js if not exist docs\data.js move /y site\data.js docs\ >nul
if exist site\ads if not exist docs\ads move /y site\ads docs\ads >nul
if not exist .github\workflows mkdir .github\workflows
copy /y daily.yml.txt .github\workflows\daily.yml >nul

if not exist .git git init -b main
git config user.name "killer7wayes-lab"
git config user.email "killer7wayes-lab@users.noreply.github.com"
git remote remove origin >nul 2>&1
git remote add origin %REPO%
rem take GitHub's history first (it commits data every day), then put this PC's files on top
git fetch origin main >nul 2>&1
git rev-parse --verify origin/main >nul 2>&1 && git reset --mixed origin/main >nul
git add -A
git commit -m "Update from PC %date% %time:~0,5%" 
git branch -M main
echo.
echo Pushing to GitHub - a sign-in window may open: sign in and click Authorize.
git push -u origin main
echo.
if errorlevel 1 ( echo PUSH FAILED - see the message above. ) else ( echo DONE - files are on GitHub. )
pause
