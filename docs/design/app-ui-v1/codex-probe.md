# Codex 应用内接入验证

日期：2026-10-06。方式：本机一次性 Python 探针启动独立 `codex app-server --listen stdio://`，使用模拟页面截图与已安装 Skill；未执行桌面输入或招聘网站操作。

## 结果

| 检查 | 实际结果 |
|---|---|
| 本机 CLI | `codex-cli 0.156.1`；帮助仍标记 App Server 为 experimental |
| 初始化 | `initialize` 与 `initialized` 握手成功 |
| 账号 | `account/read` 返回 `chatgpt`；后续模型回合实际完成 |
| Skill 发现 | `skills/list` 发现且启用 `job-search-assistant` |
| Skill 输入 | 显式传入 Skill；正确回答“薪资＋面试”整条转人工、保持锁定 |
| 图片输入 | `localImage` 正确识别模拟页面标题、两个区域、开聊/附件/回复均为 0 |
| 流式事件 | 第一轮收到 100 个 `item/agentMessage/delta`，最终 `completed` |
| 重启后恢复 | 关闭探针启动的进程，重新启动后 `thread/resume` 成功；模型准确返回首轮标记 `PROBE-6729` 及处理规则 |
| 活动回合中断 | 收到文本输出后请求 `turn/interrupt`，最终状态为 `interrupted` |
| 清理 | 探针进程已退出，测试会话已归档 |

使用本机默认模型 `gpt-6-astra`，没有修改模型配置，也没有读取或导出登录令牌。截图仅含虚构信息，但模型推理实际连接了服务端。

## 中断时序发现

首次 `turn/start` 返回后立即调用 `turn/interrupt`，收到 `-32600: no active turn to interrupt`。随后重启能看到该回合为 interrupted，但这不能证明第一次中断 RPC 成功，进程关闭也可能导致中断。

复测改为收到该回合的输出事件后中断，RPC 成功且观察到 `turn/completed.status=interrupted`。这证明活动回合可以中断；尚未证明启动期间任意时刻的中断均可靠，也不能据此认定服务端内部根因。

产品处理：独立持久化业务暂停并关闭新的输入入口，维护启动中的取消意图，按事件核对回合状态。不能只依赖一次模型中断调用，更不能收到报错后恢复桌面操作。

## 可下结论与未验证项

可采用 App Server 作为应用内 AI 会话通道；已验证账号状态、真实推理、Skill 输入、图片、流式输出、跨进程上下文恢复和活动回合中断。

尚未验证：新账号登录 UI、配额耗尽/断网、打包后的工具调用与 desktop 权限、应用崩溃期间的发送一致性、真实 BOSS/ATS、Tauri 安装包。下一阶段必须先完成模拟桌面执行闭环，再扩展业务页面。

本地原始结果位于 `.verification/app-server-probe/result.json`。探针、schema 与 stderr 属于临时验证产物，不纳入产品或公开发布；此报告保留可评审结论与失败记录。

参考：[App Server 官方文档](https://learn.chatgpt.com/docs/app-server)。实际实现应以随验证版本导出的 JSON Schema 为接口依据。
