# Tools 工具目录

此目录用于存放第三方工具文件。

## SumatraPDF.exe

SumatraPDF 阅读器可执行文件，用于静默打印PDF。

> **注意**：由于 GitHub 对仓库体积的限制，`SumatraPDF.exe` 未直接纳入仓库。

### 获取方式

请自行下载 SumatraPDF 3.4.6 或更高版本：

- 官网下载：<https://www.sumatrapdfreader.org/download-free-pdf-viewer>
- 直接下载便携版（Zip），解压后提取 `SumatraPDF.exe`

### 放置位置

将下载的 `SumatraPDF.exe` 放到本目录下（即 `tools/SumatraPDF.exe`），即可被程序自动识别。

### 用途说明

SumatraPDF 是一个轻量级开源 PDF/EPUB/MOBI 阅读器，支持命令行静默打印：

```
SumatraPDF.exe -print-to "打印机名" -silent -exit-when-done "文件路径"
```

服务端在打印 PDF 文件时会优先尝试使用 SumatraPDF 执行静默打印，
如果未找到该工具，则会回退使用 `ShellExecute` 调用系统默认打印方式。
