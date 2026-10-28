@echo off
setlocal
set /p DEVICE_HOST=请输入开发板 IP 地址（例如 192.168.1.50）: 
if "%DEVICE_HOST%"=="" (
  echo 未输入开发板 IP，安装已取消。
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_codex_companion.ps1" -DeviceHost "%DEVICE_HOST%"
if errorlevel 1 (
  echo 安装失败，请查看上方错误。
  pause
  exit /b 1
)
echo.
echo 安装完成。以后正常打开 Codex 即可使用。
pause
