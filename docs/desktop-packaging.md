# Windows 安装包构建与发布

构建入口：`python -X utf8 "tools/build_desktop.py"`。需要既有桌面开发环境和官方 Codex CLI 0.156.1 Windows x64 平台包，启动器可发现 npm 安装的官方包，或通过 `JOB_ASSISTANT_CODEX` 指向其中 `bin/codex.exe`。

脚本在项目 `.verification/desktop-release` 创建专用 Python 构建环境，安装依赖清单及 PyInstaller 6.15.0；按白名单暂存 `apps/worker` 的工作进程源码和资源，生成 onedir 工作进程。只复制 Codex 官方发行目录，不读取用户的 `.codex` 目录或凭据。

`apps/desktop/src-tauri/tauri.bundle.conf.json` 仅用于分发构建，指定内置资源。普通开发/单元测试不需要预先准备大体积发布资源。编译后的应用从安装目录 `runtime` 读取工作进程与 Codex；网页资源嵌入 exe，无需 Vite 服务。

构建可分步运行：

```powershell
python -X utf8 "tools/build_desktop.py" --resources-only
python -X utf8 "tools/build_desktop.py" --reuse-resources
```

复用资源前应确认 Python 依赖或工作进程没有变更；变更后重新执行完整构建。资源清单记录路径、大小和 SHA256，拒绝明显的账户文件、个人资料和运行台账。许可证随包提供，清单不包含构建者用户目录。

安装版将投递记录、截图、诊断、个人资料和专用浏览器数据写入安装目录的 `data`。请选择当前 Windows 用户可写的安装位置。由旧版本升级时，先退出旧的求职助手和求职专用 Chrome。首次打开后会复制旧资料并更新其中的路径，原目录可用于回退。内置 Codex 在 `data/codex` 单独保存登录和会话，升级后需要时可在应用内重新登录。

产物位于 `dist/desktop`。上传 Windows x64 MSI、当前用户 EXE、`SHA256SUMS.txt` 和 `resource-manifest.json` 为 GitHub Release 附件，Git 提交只包含源码和文档。正式版使用普通 Release，说明采用 `docs/releases/<tag>.md`。测试版用预发布标签。

发布前检查选目录、快捷方式、脱离源码启动、读取台账、正常退出及安装包校验。用 Windows Installer 检查 MSI 数据库和提取运行，并用当前用户 EXE 验证完整安装流程。真实平台测试按用户授权单独安排，Release 说明记录实站验收范围。

MSI 使用 Tauri 下载到构建目录的 WiX，并用 Windows 默认临时目录运行 Installer 校验。项目专用临时目录用于 Python 打包。

本机 MSI 检查使用 `python -X utf8 "tests/desktop_msi_smoke.py" --msi "安装包路径"`。可以用 `--wix-dark "dark.exe 的路径"` 直接提取包内文件，再核对资源哈希和启动。

EXE 安装后，运行 `python -X utf8 "tests/desktop_install_smoke.py" --shortcut "桌面快捷方式的完整路径"`。脚本通过实际快捷方式验证首页和导出。`--connect` 用于检查 Codex 连接。

新浏览器底座随包提供固定的 agent-browser 原生可执行文件、许可证、补丁说明和运行时清单，取消 `browser-extension` 分发。先运行 `tools/build_agent_browser.py` 构建固定来源，再准备桌面资源；`--reuse-resources` 会核验 工作进程源码指纹、worker 哈希、原生运行时能力及全量资源清单，不会复用已过期工作进程。清理路径必须解析在本项目资源目录内部；构建只打包白名单，不读取浏览器登录目录或 `.codex` 凭据。

配置页实际窗口回归：先运行 Vite 并构建 debug 原生程序，再运行 `python "tests/desktop_configuration_smoke.py" --profile "本机资料文件" --resume "附件文件"`。此检查只操作本应用的资料、九种配置预览、规则验证和连接器设置，不点击真实招聘网站。测试数据和截图只写入 `.verification`。
