# 求职自动化助手

Windows 求职辅助项目，提供桌面应用测试版及 Codex Skill / 插件源码包。简历与个人资料由用户提供；按轮次授权后操作，面试邀约始终转人工。

**下载桌面测试版：** [v0.3.0-beta.4 Windows x64 安装包](https://github.com/XDL1234/job-search-assistant/releases/tag/v0.3.0-beta.4)。下载 Assets 中的 `setup.exe`，可选择安装目录并创建桌面图标；无需安装 Python、Node 或 Rust。[功能范围与安装说明](docs/releases/v0.3.0-beta.4.md)。

## 项目状态与文档

既有实现为 **v0.2 Skill 与本机浏览器面板**。独立 Windows 桌面应用已发布 **v0.3.0-beta.4 测试版安装包**：三种投递路径已各完成一次真实开聊、截图与回执核验。聊天值守、附件和官网网申仍待实站验收，不能把少量开聊成功等同完整求职流程已可用。

- [文档索引与维护约定](https://github.com/XDL1234/job-search-assistant/blob/main/docs/README.md)
- [八页界面稿与设计说明](https://github.com/XDL1234/job-search-assistant/blob/main/docs/design/app-ui-v1/README.md)
- [桌面应用实施计划](https://github.com/XDL1234/job-search-assistant/blob/main/docs/superpowers/plans/2026-10-06-desktop-app-v1.md)
- [桌面开发预览与验证状态](docs/desktop-development.md)
- [后台浏览器连接与开发进度](docs/background-browser.md)
- [变更记录](https://github.com/XDL1234/job-search-assistant/blob/main/CHANGELOG.md)

桌面应用采用 Tauri 2、React、TypeScript，复用现有 Python 与 SQLite。BOSS 后台模式通过用户安装的 Chrome 扩展连接已登录页面；官网保留桌面 computer use。

已发布 `0.3.0-beta.4`：修复当前 BOSS 卡片字段和页面/回执等待，保留实习、校招、正式工作配置，以及脚本、AI 推荐、AI 精筛和官网入口。当前账号的聊天列表尚需适配，自动回复/值守暂勿作为已验收功能开启，具体以 [beta.4 说明](docs/releases/v0.3.0-beta.4.md)为准。首次须在 Chrome 加载随包连接器；升级后需在扩展管理页刷新扩展，单独刷新 BOSS 网页可能仍使用已缓存旧脚本。详见[后台连接步骤](docs/background-browser.md)。

## 单独安装 Skill（开发者与既有用户）

需要 Windows、Python 3.11+、能运行本地命令并查看图片的 Codex 会话。解压后在 PowerShell 执行：

```powershell
& "./install.ps1"
```

若本机策略不允许运行 PowerShell 脚本，不修改策略；改用：

```powershell
python -X utf8 "./skills/job-search-assistant/scripts/run.py" install
python -X utf8 "./skills/job-search-assistant/scripts/run.py" bootstrap --install
```

安装器不会覆盖已有不同版本，不全局安装 Python 包。所需电脑操作、表格、截图及回复能力内置；首次初始化将固定版本软件包装入用户专用环境。插件源码包含标准 manifest，但未发布到任何目录。

安装后在 Codex 新一轮对话中输入：

> 使用 $job-search-assistant，读取我提供的公司表格、个人信息和简历。目标是嵌入式软件工程师。先展示本轮配置供我确认，再开始执行。

第一次准备公司 `.xlsx` / UTF-8 `.csv`、已有简历文件、详细个人资料。Skill 会整理资料字段映射并确认；不会替你编造资料。配置示例中的薪资、地点和话术均为虚构，规则默认关闭，须按真实意愿启用。

## 功能与边界

### 本地可视化面板（v0.2）

在发行包目录执行以下命令，自动补齐依赖并打开浏览器：

```powershell
& "./open-dashboard.ps1"
```

面板包含运行概览、带时间的最近截图、投递记录与填写证据、待人工事项、新建任务配置、Excel 导出。暂停/停止立即写入状态；继续和仅值守由 Codex 下一次执行请求接收。已经发出的操作无法撤回。

新建任务预览并确认后，把面板给出的轮次编号交给 Codex 续接。保存人工意见不发送消息，不解除面试会话锁；在 BOSS 完成人工沟通后再点击恢复。**面板在线不代表 AI 正在执行**，最近截图也不是直播。

面板服务只监听本机，不发送个人数据到第三方。启动后保持终端打开，按 Ctrl+C 关闭面板服务。关闭浏览器标签不会停止服务或投递；停止本轮任务请使用“停止”。更多启动方法与执行器对接见 Skill 内 `references/dashboard.md`。

### 求职工作流

- 官网每家公司选择最匹配的一个岗位，逐段填写截图、提交结果和失败现场留档。
- BOSS 支持平台筛选后连续沟通或阅读职位后 AI 精筛；平台已有首次话术只触发一次，对方索要后发简历。
- 固定回复须条件匹配；非常见问题可人工或 AI 回复；面试邀约整条消息转人工。
- SQLite 记录防止盲目重复提交；独立 Excel 报告不修改原公司表。
- 电脑操作由当前 Codex 模型观察截图后调用本地脚本；不是无需模型的固定坐标机器人，也没有独立后台模型服务。
- 仅会话持续运行时值守。电脑锁屏、窗口切换或会话结束会停止/暂停操作。真实网站布局、账号权限和平台限制需要实际接入核实。

个人记录默认在用户主目录下 `.job-search-assistant`，不会进入分发包。该位置避开 Windows MSIX 对 AppData 的重定向。截图文件保存在本地；Codex 分析时会将所需资料或截图发送到模型服务，本地存储不代表离线推理。不要把运行目录分享给其他用户。

## 开发验证

```powershell
python -X utf8 -m unittest discover -s "./tests" -v
```

`tests/fixtures/sandbox.html` 是离线模拟网申与聊天页面，不发送任何外部请求。测试报告及已知未验证项见 `docs/implementation.md`。
