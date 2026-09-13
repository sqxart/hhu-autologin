@echo off
chcp 65001 >nul
cd /d "%~dp0"
title HHU-AutoLogin 安装

if not exist config.ini (
    copy /y config.example.ini config.ini >nul
    echo 已生成 config.ini，请在打开的窗口中填写学号和密码，保存后关闭本窗口。
    notepad config.ini
)

set "TASKNAME=HHU-AutoLogin"

if exist hhu_login.exe (
    schtasks /create /tn "%TASKNAME%" /tr "\"%~dp0hhu_login.exe\" --quiet" /sc minute /mo 1 /f
    goto :done
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    schtasks /create /tn "%TASKNAME%" /tr "\"pythonw.exe\" \"%~dp0hhu_login.py\" --quiet" /sc minute /mo 1 /f
    goto :done
)

echo [!] 未找到 pythonw.exe（无窗口版 Python）。
echo     直接用 python.exe 的话，每分钟会闪一个黑框。
echo     建议：先到 Releases 页下载打包好的 hhu_login.exe，再运行本脚本；
echo     或自行执行: pip install pyinstaller ^&^& pyinstaller --onefile --name hhu_login hhu_login.py
echo.
echo     仍要继续用 python.exe 吗？按任意键继续，关闭窗口取消。
pause >nul
schtasks /create /tn "%TASKNAME%" /tr "\"python.exe\" \"%~dp0hhu_login.py\"" /sc minute /mo 1 /f

:done
echo.
echo 安装完成：已创建计划任务「%TASKNAME%」（每分钟检测，掉线自动登录）。
echo 验证：运行  hhu_login.py --check
echo 卸载：运行  uninstall.bat
pause
