<p align="center">
  <img src="apps/desktop/src-tauri/icons/128x128.png" width="80" height="80" alt="求职助手图标">
</p>

<h1 align="center">求职助手</h1>

<p align="center">Windows 求职工作台，一处管理公司网申、BOSS 直聘和投递记录。</p>

<p align="center">
  <a href="https://github.com/XDL1234/job-search-assistant/releases/latest"><strong>下载 Windows 版</strong></a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="docs/README.md">项目文档</a> ·
  <a href="https://github.com/XDL1234/job-search-assistant/issues/new/choose">反馈问题</a>
</p>

![求职助手总览，展示任务进度、待办事项和近期投递结果](docs/media/desktop-overview.png)

当前开发版界面，使用模拟数据。

## 下载与安装

最新正式安装包为 **v1.1.0**，适用于 Windows x64。源码中的后续变更见[变更记录](CHANGELOG.md)。

| 安装方式 | 下载 |
| --- | --- |
| 当前用户安装，适合首次使用 | [EXE 安装包](https://github.com/XDL1234/job-search-assistant/releases/download/v1.1.0/job-search-assistant-1.1.0-windows-x64-setup.exe) |
| 使用 Windows Installer，需管理员授权 | [MSI 安装包](https://github.com/XDL1234/job-search-assistant/releases/download/v1.1.0/job-search-assistant-1.1.0-windows-x64.msi) |

安装完成后，从桌面或开始菜单打开「求职助手」。Python 工作进程和 Codex CLI 随安装包提供。升级路径、数据备份和校验方法见 [v1.1.0 安装说明](docs/releases/v1.1.0.md)。

## 快速开始

1. 在「简历」和「个人信息」保存本轮所用材料。支持从 PDF、DOCX 或扫描件整理资料。
2. 在「连接与诊断」连接求职专用 Chrome。使用 AI 填写或岗位精筛时，完成 Codex 连接。各招聘站的账号登录在 Chrome 内完成。
3. 从「新建投递」选择公司网申或 BOSS 直聘，确认公司、岗位和操作范围。执行工作台会显示进度、当前网页和需要处理的事项。

## 主要功能

| 功能 | 操作方式 |
| --- | --- |
| 公司网申 | 导入 Excel 或 UTF-8 CSV。开发版按表格顺序准备网页，登录后定位岗位并填写。仅填写模式保留表单供你核对，自动提交由任务单独授权。 |
| BOSS 直聘 | 沿用平台筛选进行沟通，或使用 AI 精筛。面试邀约进入待办，交由你处理。 |
| 简历与个人信息 | 管理投递附件和结构化资料，支持本地 OCR，并按网页字段填写已保存的资料。 |
| 工作台与投递记录 | 查看实时画面、暂停或恢复任务，并将记录导出到 Excel。提交结果不明确时标记为待核查。 |

[公司网申流程](docs/design/company-web-ai-fill.md) · [浏览器连接说明](docs/background-browser.md)

## 数据存放

个人资料、投递记录和历史截图默认位于安装目录的 `data` 文件夹。简历 OCR 在本机运行。使用 Codex 分析时，相关资料或画面会发送到模型服务。

## 源码与开发

- `apps/desktop` 存放 React、TypeScript 界面和 Tauri/Rust 桌面程序。
- `apps/worker` 存放 Python 工作进程、SQLite 台账和模拟页面资源。
- `tests` 提供单元测试、桌面验证和两套模拟网申表单。

[源码启动与测试](docs/desktop-development.md) · [Windows 打包](docs/desktop-packaging.md) · [变更记录](CHANGELOG.md)
