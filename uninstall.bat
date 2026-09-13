@echo off
chcp 65001 >nul
schtasks /delete /tn "HHU-AutoLogin" /f 2>nul
echo 已删除计划任务「HHU-AutoLogin」。
echo 如需彻底清理，可删除本目录（config.ini 里有你的密码）。
pause
