@echo off
chcp 65001 >nul
cd /d "%~dp0"
python wow_damage_app.py %*
pause
