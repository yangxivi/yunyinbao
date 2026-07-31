@echo off
chcp 65001 >nul
echo ========================================
echo   云印宝 - 一键打包脚本
echo ========================================
echo.

echo [1/3] 正在安装依赖...
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo 依赖安装失败！
    pause
    exit /b 1
)
echo 依赖安装完成！
echo.

echo [2/3] 正在打包服务端...
pyinstaller --clean 云印宝服务端.spec
if errorlevel 1 (
    echo 服务端打包失败！
    pause
    exit /b 1
)
echo 服务端打包完成！
echo.

echo [3/3] 正在打包客户端...
pyinstaller --clean 云印宝客户端.spec
if errorlevel 1 (
    echo 客户端打包失败！
    pause
    exit /b 1
)
echo 客户端打包完成！
echo.

echo ========================================
echo   打包完成！
echo   服务端: dist\云印宝服务端.exe
echo   客户端: dist\云印宝客户端.exe
echo ========================================
pause
