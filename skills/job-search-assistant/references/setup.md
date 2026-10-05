# 安装、初始化与运行环境

本包有一个主 Skill。电脑操作、表格和证据处理是随包的 Python 模块与参考说明，无需另装同名 computer-use、spreadsheets 或内部开发用 Skills。`dependencies.json` 是本项目清单，不是 Codex 的原生递归依赖格式。外部 `skills` 列表为空是有意设计，所有需要的能力已经内置。

## 可分发安装

用户解压发行包后调用根目录 `install.ps1`，或者让已有 Codex 读取本 Skill。安装器把技能复制到用户 `.agents/skills/job-search-assistant` 并初始化依赖。已有相同文件直接复用，不覆盖不同版本；版本冲突报告路径，等待使用者选择。插件包也包含 `.codex-plugin/plugin.json`，可作为本地插件源码分发；没有自动发布到插件目录。

## 首次调用

```powershell
python -X utf8 "<skill>/scripts/run.py" bootstrap --install
```

只有 Python 标准库参与此步骤。需要 Windows 与 Python >=3.11。Python 不存在时给出官方安装器要求，不能静默全局安装。默认用户数据位置为用户主目录下 `.job-search-assistant`，避开 MSIX 宿主的 AppData 虚拟化；也可在子命令之前指定 `--root "<绝对目录>"`，之后每个命令使用同一个 root。

初始化为依赖清单生成指纹，在 `environments/<指纹>` 建立独立 venv；使用官方 PyPI 与固定运行时版本。已就绪时不重复下载。改变版本会建立新环境，不升级系统包或覆盖既有环境。网络失败报告错误并保留可恢复的环境，重新调用继续。安装只是准备工具，不授权投递。

输出 JSON 中 `result.python` 是后续必须使用的 Python。用它执行：

```powershell
"<返回的 python 路径>" -X utf8 "<skill>/scripts/run.py" doctor
```

PowerShell 中执行带引号的可执行路径需在前面加 `&`。`doctor` 验证依赖、SQLite 与 Excel 写入，桌面真实操作需另外验证。不要将 ready 字段理解为平台登录或所有 UI 已验证。

## 桌面要求

Chrome/Edge/Firefox/Brave 位于主显示器、可见、电脑未锁屏。用户先完成账号登录；不要读取或导出 Cookies。绑定浏览器后可切换同一窗口内的标签；用户切换其他应用会触发暂停，不能自行夺回前台。重启浏览器需重新绑定。

截图只包含绑定浏览器窗口，坐标相对于图片左上角，保留原始尺寸。输入后重新查看截图，确认中文、标点、选项和附件正确。鼠标移至主屏角落可以触发紧急停止；保持此功能启用。

## 个人数据与分发

`records.sqlite3`、截图、资料和结果表位于用户运行目录。包内只有虚构示例，不把运行目录纳入发行 ZIP。首次使用只安装本技能所需的本地包；MCP、浏览器扩展、系统权限若另有宿主要求，必须遵从，不能声称全部静默安装。
