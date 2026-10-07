# Windows 测试版构建与发布

构建入口：`python -X utf8 "tools/build_desktop.py"`。需要既有桌面开发环境和官方 Codex CLI 0.156.1 Windows x64 平台包，启动器可发现 npm 安装的官方包，或通过 `JOB_ASSISTANT_CODEX` 指向其中 `bin/codex.exe`。

脚本在项目 `.verification/desktop-release` 创建专用 Python 构建环境，安装依赖清单及 PyInstaller 6.15.0；通过 Skill 白名单暂存资源，生成 onedir 工作进程。只复制 Codex 官方发行目录，不读取用户的 `.codex` 目录或凭据。

`apps/desktop/src-tauri/tauri.bundle.conf.json` 仅用于分发构建，指定内置资源。普通开发/单元测试不需要预先准备大体积发布资源。编译后的应用从安装目录 `runtime` 读取工作进程与 Codex；网页资源嵌入 exe，无需 Vite 服务。

构建可分步运行：

```powershell
python -X utf8 "tools/build_desktop.py" --resources-only
python -X utf8 "tools/build_desktop.py" --reuse-resources
```

复用资源前应确认 Python/Skill/内置执行器没有变更；变更后重新执行完整构建。资源清单记录路径、大小和 SHA256，拒绝明显的账户文件、个人资料和运行台账。许可证随包提供，清单不包含构建者用户目录。

产物在 `dist/desktop`：安装器、`SHA256SUMS.txt` 和 `resource-manifest.json`。不把这些大型二进制提交到 Git，上传为 GitHub Release 附件。使用明确版本标签，Release 勾选 **pre-release**，标题含“测试版”，说明采用 `docs/releases/<tag>.md`。

预发布前至少检查：实际安装至指定目录、快捷方式指向正确 exe、脱离源码和开发环境的启动、读取台账、正常退出、包内不含用户数据。真实平台测试按用户授权范围单独安排，测试版发布不能隐含承诺真实投递已可用。

本机安装启动检查脚本：`python -X utf8 "tests/desktop_install_smoke.py" --shortcut "桌面快捷方式的完整路径"`。脚本临时移除开发环境变量，通过快捷方式启动实际安装版，验证首页与导出后关闭，不创建投递任务。`--connect` 仅在发布后用于检查真实 Codex 连接；不触发模型推理和消息发送。
