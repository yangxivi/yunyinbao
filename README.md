# 云印宝 - 私有化智能云打印系统

云印宝是一套私有化部署的智能云打印系统，基于 Python + Tkinter + Flask 技术栈，实现局域网/外网跨设备共享打印。

## 系统组成
- **服务端**：连接打印机的 Windows 主机，管理打印机、调度任务、提供 API
- **客户端**：员工电脑安装使用，连接服务端发起打印，支持虚拟打印机
- **Web管理后台**：Flask 网页端，用户管理、打印统计、系统设置

## 快速开始
```bash
pip install -r requirements.txt
python server_main.py      # 服务端
python client_main.py      # 客户端
python web_main.py         # Web后台: http://localhost:8990  admin/admin123
```

## 打包
```bash
build_all.bat    # 需要已安装 PyInstaller
```

## 目录结构
```
common/         公共模块(config/database/theme/utils/autostart/print_engine/virtual_printer)
server/         服务端(打印机管理/任务调度/设备管理/API/GUI)
client/         客户端(API连接/虚拟打印机/GUI)
web/            Flask Web管理后台
    web_templates/  HTML模板
data/           配置与数据
tools/          SumatraPDF.exe
server_main.py  服务端入口
client_main.py  客户端入口
web_main.py     Web后台入口
```

GitHub: https://github.com/yangxivi/yunyinbao
