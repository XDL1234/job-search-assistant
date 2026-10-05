# 求职助手桌面应用第一版实施计划

> **For agentic workers:** 使用 `superpowers:executing-plans` 在当前会话逐项实施；用户另行选择并行代理时才使用 `superpowers:subagent-driven-development`。以复选框记录实际完成情况。

**Goal:** 将已确认的八页设计实现为可直接驱动求职流程、可人工接管的 Windows 应用。

**Architecture:** Tauri 托管 React 界面、Codex App Server 及打包后的 Python 执行入口。复用既有 Python/SQLite 领域逻辑，以受限 IPC 连接应用交互；先贯通一条模拟业务流程，再逐页扩展。

**Tech Stack:** Tauri 2、React、TypeScript、Vite、shadcn/ui、Tailwind、Python、SQLite、PyInstaller、Codex App Server。

**Spec:** [桌面应用设计](../../design/app-ui-v1/desktop-spec.md)，[已确认图册](../../design/app-ui-v1/index.html)，[接入验证](../../design/app-ui-v1/codex-probe.md)。

## 全局约束

- Windows、本地单用户、单个可见浏览器执行队列。
- 每轮确认范围后执行；材料、范围改变后重新确认。
- 面试安排始终由用户决定；确认发送不自动解锁。
- 成功必须有对应证据；不确定结果禁止盲目重发。
- 关闭应用先暂停并保存状态，下次打开不自动继续投递。
- 普通用户无需手动安装 Python 或寻找额外 computer-use Skill。
- 不自动改写简历，不修改原公司表，个人数据不进入源码或发布包。
- 保留已有 Skill/CLI 入口；不复制领域逻辑，不实现云同步和多用户。
- 文档任务与开发任务分开验收；本计划尚未开始产品实现。
- 不自动执行 git 分支、提交或推送；开发环境的全局安装单独说明影响。

## 重点验证

1. 刚启动就暂停、断线或退出：持久化暂停先于模型取消，后续输入被拒绝（任务 2、3）。
2. 用户确认发送期间对方发来新消息：旧确认失效，人工锁保持（任务 6）。
3. 发出消息后未收到回执就崩溃：显示待核查，不再次发送（任务 3、6）。
4. 中文/空格路径、含表格的 Word、扫描 PDF、重复或空白公司行：保留出处并明确失败，不生成虚假字段（任务 4、5、8）。
5. 多窗口/缩放变化、从应用切回浏览器、两份程序同时运行：重新观察目标窗口且只允许一个输入队列（任务 3、8）。

## 文件职责与接口

新增 `apps/desktop/`，不把旧 HTML 面板套进桌面窗口：

| 文件或目录 | 职责 |
|---|---|
| `apps/desktop/src/app.tsx`、`src/styles.css` | 八页导航、公共布局、视觉变量 |
| `apps/desktop/src/bridge.ts`、`src/contracts.ts` | 前端调用与类型；不暴露任意 shell |
| `apps/desktop/src/pages/{Home,Profile,Companies,Boss,Replies,Running,Inbox,History}.tsx` | 各页面用户流程 |
| `apps/desktop/src/components/` | 状态条、任务确认、证据预览等复用组件 |
| `apps/desktop/src-tauri/src/worker.rs` | 参数数组启动 Python sidecar、解析结果、超时与退出 |
| `apps/desktop/src-tauri/src/codex.rs` | App Server stdio 协议与事件解码 |
| `apps/desktop/src-tauri/src/executor.rs` | 轮次与 thread 映射、串行调度、暂停恢复 |
| `apps/desktop/src-tauri/src/lifecycle.rs` | 窗口关闭、单实例、子进程退出 |
| `skills/job-search-assistant/scripts/job_assistant/app_service.py` | 应用专用白名单请求，复用现有领域模块 |
| 同目录 `profiles.py`、`human_replies.py`、`migrations.py` | 资料版本、人工发送授权、可回滚迁移 |
| `tools/build_desktop.py`、`tools/job-assistant-worker.spec` | Python 工作进程与桌面发布产物 |

边界契约先固定，业务数据类型随对应任务补齐：

- `UiRequest = {id: string, method: UiMethod, params: object}`；`UiMethod` 是白名单，不能透传任意 CLI 操作。
- `WorkerResponse = {id: string, ok: boolean, result?: object, error?: {code: string, message: string}}`。
- Python `handle_app_request(store: Store, request: dict) -> dict`；由 `app-request --file <UTF-8 JSON>` 入口调用。保持既有 `request --file` 供 Skill 执行使用。
- Rust `WorkerClient.request(request: UiRequest) -> Result<WorkerResponse, BridgeError>`；创建受限临时请求文件、使用参数数组调用 sidecar，不拼 shell 文本。
- Rust `CodexClient.request(method: &str, params: serde_json::Value) -> Result<serde_json::Value, BridgeError>`；只供原生协调器使用，不对网页暴露任意 RPC。
- `ExecutorEvent = {runId: string, seq: number, type: 'state' | 'message' | 'attention' | 'error', payload: object}`。重连后重读业务快照，不能依赖丢失的事件补全结果。
- React `request<T>(method: UiMethod, params: object): Promise<T>`，错误包含可显示信息；磁盘路径、凭据不进入常规日志。

## 任务 1：最小桌面框架与业务读取

**文件：** 创建 `apps/desktop/package.json`、锁文件、Vite/TypeScript 配置、`src/app.tsx`、`src/bridge.ts`、`src/contracts.ts`、`src/styles.css`；创建 `src-tauri/{Cargo.toml,tauri.conf.json,capabilities/default.json,src/lib.rs,src/worker.rs}`；创建 `app_service.py`；修改 `cli.py` 增加 `app-request` 子命令。

**输入/输出：** `handle_app_request` 先支持 `health`、`snapshot`、`export_records`，通过既有 `Dashboard.snapshot`、`Store.export` 读取临时台账；不启动真实任务。

- [ ] 检测 Rust/MSVC/WebView2 和构建工具，列出缺项；依赖锁定到本次实际验证版本，不使用未锁定 latest 作为发布依据。
- [ ] 在 `tests/test_app_service.py` 写 `test_unknown_method_rejected` 与 `test_snapshot_is_read_only`：未知方法拒绝；读状态前后无新增轮次、授权或动作。
- [ ] 运行 `python -m unittest discover -s tests -p test_app_service.py -v`，确认缺少实现导致失败，再实现白名单入口与响应包。
- [ ] 创建 Tauri 框架与统一视觉变量，八页先有导航；首页和记录页接入真实临时台账，无数据时显示空态。
- [ ] 运行上述 Python 测试、前端 `npm run typecheck`、`npm run build`、原生 `cargo test`；成功打开 Windows 窗口并读取快照。

**验收：** 应用能打开，布局与已确认稿一致，显示真实数据或空态，关闭不留下本应用工作进程。

## 任务 2：应用内 Codex 连接与登录状态

**文件：** 创建 `src-tauri/src/codex.rs`、`src/components/ConnectionStatus.tsx`，修改 `src-tauri/src/lib.rs`；创建 `src-tauri/tests/codex_protocol.rs`。

**输入/输出：** 实现 `CodexClient.request` 与通知流；`account/read` 得到就绪状态；登录操作使用服务端返回的登录流程。只持久化必要的会话标识，不复制凭据。

- [ ] 协议测试覆盖拆行/多行读取、响应与通知交错、错误响应、EOF、请求超时；`turn/start` 刚返回时取消要保留取消意图，不能恢复执行状态。
- [ ] 运行 `cargo test --test codex_protocol` 观察预期失败后，实现 stdio 进程、请求 ID 匹配、事件分发和退出处理。
- [ ] 连接就绪 UI 区分未安装、版本不兼容、未登录、正在登录、已连接、断线；未安装时提供明确安装步骤，不静默全局安装。
- [ ] 使用模拟图片重跑本轮已验证的 Skill/图片问答、流式、中断、跨进程恢复。新账号登录在隔离测试环境验收，不能退出开发者当前账号来测试。
- [ ] 运行协议测试与前端检查，保留实际 CLI 版本和失败信息。

**验收：** 点击应用中的测试入口可得到流式结果；重启能恢复测试上下文；失败有明确状态，不伪装成执行中。

## 任务 3：首条完整执行流程与暂停接管

**文件：** 创建 `src-tauri/src/executor.rs`、`src-tauri/src/lifecycle.rs`、`src/pages/Running.tsx`、`migrations.py`；修改 `app_service.py`、`panel_state.py`、`cli.py`、`references/setup.md`、`references/commands.md`；创建 `tests/test_app_execution.py`、`tests/desktop_app_smoke.py`。

**输入/输出：** `preview_run(config) -> {ticket, config}`、`start_run(ticket) -> {run_id}` 复用现有预览/授权检查；`control_run(run_id, action)` 接受 `paused/running/watching/stopped`。原生 `ExecutorCoordinator` 提供 `start_run/pause_run/resume_run/stop_run`，维护模型 ID 与轮次映射。

- [ ] 为新映射表建立版本迁移：测试旧库备份、迁移失败回滚与重复启动幂等，再实现最小迁移。
- [ ] 用临时台账测试“开始后立即暂停”“恢复前资料改变”“暂停前截图被复用”“发送后崩溃不重发”“两个执行器竞争”；失败后补充协调器和动作边界检查。
- [ ] 将安装目录下 Python 执行入口与 Skill 路径传入 App Server 任务上下文，在模拟页面真实调用 `capture/act` 并实际读取图片。核验 sandbox 和桌面权限；通路失败时保持未就绪状态，先解决此里程碑再扩展页面。
- [ ] 沿用已有 `prepare/claim/finish`、窗口绑定和全局桌面锁；协调器暂停先更新 SQLite，再取消模型。控制入口不排在模型长任务后面。
- [ ] 实现运行页队列、最近画面时间、步骤、暂停/接管/恢复/停止。停止与断线保存检查点；关闭先暂停，程序重启等待用户恢复。
- [ ] 测试模拟网申成功与失败、混合面试消息转人工、截图/字段对应、结果导出；运行 `python -m unittest discover -s tests -p test_app_execution.py -v` 与 `python tests/desktop_app_smoke.py`。

**验收：** 第一条可演示流程由应用发起并真正控制本地模拟浏览器；暂停期间无新增桌面输入。仅协议问答不能通过本任务。

## 任务 4：资料导入、核对与版本保存

**文件：** 创建 `profiles.py`、`src/pages/Profile.tsx`、`tests/test_profiles.py`、小型虚构 PDF/DOCX/JSON fixtures；修改 `app_service.py`、依赖锁与打包配置。

**接口：** `extract_profile(path: Path) -> {fields, conflicts, warnings}`；字段含值及出处。`save_profile(fields: list, source_hashes: dict) -> {version_id, profile_path}`。保存结果兼容现有 JSON 使用方式，保留版本不可变快照。

- [ ] 测试文字 PDF 页码、DOCX 段落和表格、JSON 路径、字段冲突、扫描件无文本、损坏/加密文件、中文文件名；缺值不自动补齐，错误不产生已确认版本。
- [ ] 运行 `python -m unittest discover -s tests -p test_profiles.py -v`，再实现本机提取、出处映射、冲突展示与保存。选择成熟解析库并核对许可证、Python 兼容性，版本进入锁文件。
- [ ] 按第二张图实现导入与核对；保存前显示未确认项；后续用户修改资料生成新版本，不改已有轮次快照。
- [ ] 运行上述测试与前端检查，人工对照源文件检查提取结果。

**验收：** 用户无需重复录入已有资料，能够清楚确认 AI 将使用哪些字段，解析失败有具体处理入口。

## 任务 5：公司网申、BOSS 双模式与回复规则

**文件：** 创建 `src/pages/Companies.tsx`、`Boss.tsx`、`Replies.tsx`、`src/components/RunConfirmation.tsx`、`tests/test_app_configuration.py`；扩展 `app_service.py`，必要时修改 `inputs.py`。

**接口：** `read_companies`、`preview_run/start_run` 沿用任务 3；`match_reply(text, rules, profile) -> {action, reason, text?}` 复用 `policy.decide_reply`，此接口只测试规则，不建立发送动作。

- [ ] 测试公司列映射、空白/重复行、两个同名不同网址、公司表变更、双击开始、两种 BOSS 模式、数量上限和规则优先级。重复行需明确提示，不悄悄增加投递次数。
- [ ] 实现表格导入/选择、模式选择、偏好和规则表单；长配置在授权预览中可核对，不隐藏简历/资料版本。
- [ ] 回复试匹配复用现有规则判断，面试优先，条件不确定转人工。第一句沿用平台话术，界面不提供自动生成额外开场白。
- [ ] 运行 `python -m unittest discover -s tests -p test_app_configuration.py -v`，以及前端交互测试；表单提交产生的配置必须与执行器快照一致。

**验收：** 三个投递入口均能生成可核对、可执行的轮次；用户能区分推荐投递与 AI 精筛。

## 任务 6：应用内人工回复与独立解锁

**文件：** 创建 `human_replies.py`、`src/pages/Inbox.tsx`、`tests/test_human_replies.py`；修改 `app_service.py`、`store.py`、`cli.py` 和迁移。

**接口：** `approve_reply(store, run_id, conversation_id, message_key, text) -> {approval_id}`；`prepare_human_reply(store, approval_id) -> {action_id}`。授权绑定当前原文哈希，最长 10 分钟未消费即失效，点击确认时显示有效范围。人工发送走专用受控分支，发送成功后会话仍为 `human`。

- [ ] 测试草稿不发送、面试必须用户确认、新消息使旧授权失效、篡改文本被拒、过期拒绝、重复消费拒绝、崩溃待核查、发送后仍锁定、明确恢复才解锁。
- [ ] 运行 `python -m unittest discover -s tests -p test_human_replies.py -v`，再实现授权记录和一次性消费；不能修改自动路径使所有人工锁失效。
- [ ] 实现收件箱三项独立操作：保存草稿、确认发送、恢复自动处理；确认发送前展示接收人、对应来信和最终原文。
- [ ] 在模拟 BOSS 会话中输入并发送已确认文本，核验气泡、记录证据；发送期间收到新消息则转待处理，不自动换用新上下文。

**验收：** 面试决策和内容来自用户，执行器只发送已确认内容，不越过会话锁。

## 任务 7：运行可视化、历史记录与首页收口

**文件：** 完成 `src/pages/Home.tsx`、`Running.tsx`、`History.tsx`，创建 `src/components/EvidenceViewer.tsx`；扩展 `app_service.py` 的受限图片读取与查询；创建 `tests/test_app_history.py`、前端页面测试。

**接口：** `history(filters) -> {items, total}`；`read_image(kind, id)` 复用现有白名单图片读取；`export_records()` 复用导出，不写回输入公司表。

- [ ] 测试空记录、失败记录、待核查记录、证据缺失、无权路径/损坏文件、跨轮次筛选、迟到事件；前端从快照恢复真实状态。
- [ ] 实现列表到详情再到字段截图的浏览，失败与不确定记录必须保留；通知点击定位对应待办。
- [ ] 对照八图验收布局，补齐加载/空态/断线/出错/处理中/禁用状态；信息溢出用可读详情而非截断后无入口。
- [ ] 运行历史接口测试、前端检查及模拟完整流程。视觉变化仅统一组件细节，不重新设计已确认页面结构。

**验收：** 用户能从首页开始任务，在运行页知道当前情况，在记录页解释每次投递的结果。

## 任务 8：打包安装与真实环境验收

**文件：** 创建 `tools/build_desktop.py`、`tools/job-assistant-worker.spec`、桌面安装说明；更新 `README.md`、Tauri bundle 配置与资源清单；创建 `tests/test_desktop_release.py`。

- [ ] 检查发布包资源清单：含已验证 Skill、Python 依赖和所需运行文件；不含本机资料、凭据、截图和 `.verification`。
- [ ] 用 PyInstaller 打包同一入口，保留 `request`/`app-request` 能力；Tauri sidecar 按目标架构命名。安装过程不覆盖用户已有不同版本 Skill。
- [ ] 在无 Python/Node 的干净 Windows 环境安装，检查 Codex 发现/安装引导、登录、中文路径、DPI、浏览器切换、单实例、关闭后子进程退出。
- [ ] 使用旧版台账副本测试导入和手动升级，失败时数据可恢复；原用户文件不被改写。
- [ ] 运行 `python -m unittest discover -s tests -v`、前端测试/类型检查/构建、`cargo test`、桌面模拟端到端验收与安装包 smoke；记录版本和未验证环境。
- [ ] 最后在用户提供真实资料并确认本轮范围后做少量真实网站验证，分别记录 BOSS 与选定 ATS 的结果。未经此步骤，不宣称真实平台已兼容。

**验收：** 用户可下载安装并完成模拟闭环，能保留个人数据升级；真实网站支持范围依据实际记录说明。

## 本轮交付与下一步

- [x] 固定八页视觉稿与三项产品选择。
- [x] 验证本机 App Server 的真实推理、Skill、图片、流式、中断与跨进程恢复。
- [x] 梳理已有代码复用边界，并保存设计与实施清单。
- [ ] 从任务 1 开始实现应用，再完成任务 2、3 的最小闭环。

应用代码、构建依赖安装和 Git 提交均不属于本轮已完成内容。
