@echo off
chcp 65001 >nul
echo ======== 云印宝 一键打包 ========
echo [1/3] 安装依赖 ...
pip install -r requirements.txt pyinstaller >nul
echo [2/3] 打包服务端 ...
pyinstaller --clean 云印宝服务端.spec
echo [3/3] 打包客户端 ...
pyinstaller --clean 云印宝客户端.spec
echo Done. 产物在 dist\ 目录下
pause
