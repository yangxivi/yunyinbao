# tools/

本目录用于存放服务端 **PDF 静默打印** 所需的第三方引擎 `SumatraPDF.exe`。

## 为什么不在 Git 里
该文件体积较大（约 16MB）且属于第三方软件，因此不纳入版本库。克隆仓库后需自行放入。

## 放在哪里
把下载到的 `SumatraPDF.exe`（便携版即可）放到本目录：

```
tools/SumatraPDF.exe
```

## 服务端如何找到它
`server/print_engine.py` 会在以下位置按顺序查找，单文件打包模式也能正确定位：

1. 单文件运行解压目录 `sys._MEIPASS/tools/SumatraPDF.exe`
2. 可执行文件同级目录 `tools/SumatraPDF.exe`
3. 源码运行时的项目根 `tools/SumatraPDF.exe`

## 下载地址
https://www.sumatrapdfreader.org/download-free-pdf-viewer

## 重要
缺少该文件时，PDF 打印会回退到系统默认阅读器并**弹出窗口**（非静默）。
打包服务端（`云印宝服务端.spec`）前请务必确认它已就位，否则客户端提交任务时服务端会弹窗。
