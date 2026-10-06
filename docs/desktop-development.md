# 桌面开发预览

2026-10-06，Windows x64。本轮实现实施计划的任务 1–3，尚未完成任务 3 的完整 AI 模拟验收。当前工程版本为 `0.3.0-dev.1`，稳定 Skill 仍为 `0.2.0`。

## 启动

当前是需要源码和开发环境的原生应用预览，尚未提供普通用户安装包。前置环境为 Node、Python 3.11+、Rust/MSVC、WebView2，以及已登录的 Codex CLI。依赖版本由 npm/Cargo 锁文件固定；本版动态工具协议仅验证 `codex-cli 0.156.1`。

首次准备前端依赖：

```powershell
npm.cmd --prefix "./apps/desktop" ci
```

在仓库根目录启动：

```powershell
& "./open-app.ps1"
# 若脚本策略受限，无需修改系统策略：
python -X utf8 "./tools/desktop_dev.py" dev
```

启动器运行 Vite 和 Tauri 开发窗口，首次原生编译需要时间。保留启动终端；关闭应用会暂停当前任务并清理本应用的模型进程。开发服务器由启动命令管理。

本机经用户确认安装的 Rust 位于 `D:/Development/Rust`，MSVC 位于 `D:/Development/BuildTools`；启动器仅对子进程设置 Rust 路径，不改全局 PATH。Microsoft 安装器、共享 Windows SDK 和系统注册组件可能仍使用系统目录，不能宣称系统盘零写入。

开发数据默认在仓库 `.verification/desktop-app/data`，与现有 Skill 数据隔离。已有 Skill 私有 Python 环境可复用；缺少依赖时先按 Skill 安装说明初始化。可通过 `JOB_ASSISTANT_DATA_DIR`、`JOB_ASSISTANT_PYTHON`、`JOB_ASSISTANT_CODEX` 指定已有位置，不把账户凭据写入项目配置。

## 已实现的流程

- 首页、八页导航、执行工作台、只读消息中心、历史与证据、Codex 连接设置。
- 新建本地演示 → 核对虚构资料与操作范围 → 创建暂停状态的轮次 → 打开模拟页面 → 用户开始或恢复。
- 启动后留 5 秒切换浏览器；只操作标题匹配且已绑定的模拟窗口。当前需要用户保持浏览器前台。
- 原生协调器提供白名单动态工具，复用已有 Python computer use、动作台账及面试转人工逻辑。
- 暂停/停止先阻断业务输入，退出后保持暂停。恢复必须重新核验材料和截图；已发出但结果不明的动作不得盲目重发。
- 失败回合持久化错误；重新打开仍可看到原因。连接成功不代表模型有可用额度。

我的资料、公司网申、BOSS 条件和回复设置目前明确显示“尚未开放”。应用内人工编辑并确认发送、完整历史筛选、打包分发是后续任务，不因存在导航而视为已实现。

## 验证及实际阻断

| 验证层 | 结果与限制 |
|---|---|
| Python | 57 项通过，含票据一次消费、材料变更、备份回滚、恢复待核查、截图轮次和恢复边界 |
| 前端 | 类型检查、生产资源构建与 3 项桥接测试通过 |
| Rust | 6 项协议测试通过：响应/通知/工具请求、拆行、超时、EOF、早期取消、失败结果与连接代次 |
| 原生窗口 | 实际打开 Tauri/WebView2，读取快照；真实 Codex 登录状态连接曾通过 |
| 离线协议替身 | 窗口中开始即暂停、重复恢复、额度错误显示、关闭与重开保持暂停通过；初始化中关闭的子进程泄漏先复现、修复后通过 |
| 完整 AI 模拟 | **未通过**。早期尝试因浏览器前台状态停止；修正测试窗口后，真实服务返回 `usageLimitExceeded`，后续连接又返回 `workspace routing discovery timed out`。没有两次成功投递记录，不作为成功演示 |
| 发布与真实网站 | 未做 Python sidecar 打包、干净机器安装、真实 BOSS/ATS 验证 |

离线协议替身仅测试协调器与窗口生命周期，不会调用模型或实际点击投递，不能替代完整 AI 模拟验收。

检查命令：

```powershell
python -X utf8 -m unittest discover -s "./tests" -v
npm.cmd --prefix "./apps/desktop" test
npm.cmd --prefix "./apps/desktop" run build
python -X utf8 "./tools/desktop_dev.py" test
```

原生 smoke 需先保持 `npm.cmd --prefix "./apps/desktop" run dev` 在运行，并执行 `python "./tools/desktop_dev.py" build`。测试额外需要 Playwright/Chrome；仅该测试启用 WebView2 调试端口：

```powershell
# 真实连接与界面：
python -X utf8 "./tests/desktop_app_smoke.py"
# 真实 AI 与鼠标键盘，仅操作本地虚构页面：
python -X utf8 "./tests/desktop_app_smoke.py" --execute
```

离线协议测试还需通过同样的 Rust 子进程环境运行 `cargo build --example protocol_fixture`，再使用 `--protocol-fixture --lifecycle` 或 `--protocol-fixture --close-connecting`。测试报告、截图和临时数据均留在被忽略的 `.verification/desktop-app`，不随代码分发。

## 后续验收

待 Codex 额度与连接恢复后，优先重跑完整模拟：实际填写、逐字段截图、一次网申、一次沟通、面试转人工，再验证活动回合暂停及跨进程恢复。成功前不勾选任务 3，不扩展真实投递入口。

随后按任务 4–8 接入资料导入、配置与回复、人工发送、运行与记录页细化、Python sidecar 和 Windows 安装包。运行页目前显示全局最近截图，按选择轮次过滤和独立页面组件整理留在任务 7，切换任务时应结合截图标题和时间核对。
