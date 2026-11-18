# 命令接口与 JSON 请求

所有命令使用 bootstrap 返回的 Python，`--root` 位于子命令前。请求内容用文件工具写为 UTF-8 JSON，通过 `--file` 传入，不能拼接聊天文字到 shell。以下路径中的 `<skill>`、`<data>` 是运行时解析的绝对路径。

```powershell
& "<python>" -X utf8 "<skill>/scripts/run.py" --root "<data>" request --file "<data>/request.json"
```

返回 `{"ok":true,"result":...}`；失败非零退出并返回 `error`。文件路径应使用绝对路径。注意保存新返回的 run_id、attempt_id、action_id、evidence_id，不能猜测。

## 主要请求

| operation | 必需参数 | 结果/含义 |
|---|---|---|
| companies | path；可选 columns、sheet | 只读公司表，返回公司、网址、源行 |
| start | config、approval | 保存本轮授权、材料指纹，返回 run_id |
| status | 无 | 所有持久化记录及面板队列/活动/人工意见；runs.config 为对象；仅本地查看 |
| control | run_id、status | running / watching / paused / stopped。桌面任务停止后，用户可在工作台编辑并重新确认范围，再继续原任务。 |
| begin | run_id、company、url、job、channel、job_key | 创建尝试；重复受阻时恢复已存在记录 |
| attach | run_id、attempt_id | 新一轮授权后继续处理历史申请/会话，保留原始归属与去重记录 |
| field | attempt_id、name、value、source | 登记实际填充值，修改后旧截图覆盖失效 |
| evidence | attempt_id、path、kind、fields（可选数组） | 复制原始 PNG，记录哈希及字段覆盖 |
| prepare | run_id、attempt_id、kind、recipient、payload | 先写入行动；kind 为 greet/submit/reply/resume |
| finish | action_id、status、evidence_id、reason | succeeded / not_done 必须有截图和凭据说明；不明用 uncertain |
| attempt_status | attempt_id、status、reason | failed / needs_user / uncertain / no_match / skipped / in_progress |
| verify_materials | run_id | 核验当前简历与资料和授权版本相同 |
| route_message | run_id、conversation、message_key、text、analysis；可选 draft、sources | 保存最新消息并给出 reply/resume/human/skip |
| handoff | conversation、reason | 锁定人工接管，返回是否新事项 |
| resume_conversation | conversation、confirmation | 用户明确确认后解锁 |
| select_job | jobs、mode | filtered 取未沟通第一项；screened 选硬条件满足的最高分 |
| notify | subject、reason | 请求桌面提醒，仍需在聊天中提示 |
| export | 无 | 更新个人数据目录下 results.xlsx |
| tick | run_id | 接收面板继续指令，取得轮询间隔及 human_notes；实际观察与等待由会话执行 |

## 示例：启动

复制 assets/config.example.json 到个人数据目录，按用户真实条件更新，不直接使用虚构条件。首次授权前展示配置的中文摘要。

桌面 BOSS 的 `cities` 是可选的城市数组，例如 `["深圳"]`。留空沿用平台结果；填写后发送前仍核对城市。明确异地岗位跳过，地点不明不能由脚本自动认定匹配；AI 精筛可结合完整详情判断。

```json
{
  "operation": "start",
  "approval": "用户实际确认原文及本轮范围",
  "config": {
    "channels": ["web", "boss"],
    "company_table": "C:/求职/公司.xlsx",
    "profile_path": "C:/求职/个人信息.json",
    "resume_path": "C:/求职/简历.pdf",
    "target_roles": ["嵌入式软件工程师"],
    "boss_mode": "filtered",
    "max_contacts": 20,
    "unknown_reply_mode": "human",
    "poll_seconds": 60,
    "rules": []
  }
}
```

`columns` 可指定 `{"company":"企业","url":"入口","job":"职位"}`；无指定则识别常用中英文列名。CSV 必须 UTF-8。多个表格/多账号需各自独立数据目录；第一版不并行。

## 电脑操作

`bind` 需 expected_title，是用户指定的前台浏览器标题子串。`capture` 无参数，返回 path、window、screen。图片坐标相对窗口，原始尺寸。用户的其他应用在前台时 bind 不会强行切换窗口。

```json
{
  "operation": "act",
  "run_id": "从 start 返回的值",
  "purpose": "fill",
  "attempt_id": "从 begin 返回的值",
  "frame": "最近已查看的截图绝对路径.png",
  "input": {"op": "type", "text": "从用户资料读取的真实内容"}
}
```

input 支持：`click`（x、y）、`scroll`（x、y、clicks，-12 到 12）、`type`（text）、`key`（keys 数组，例如 `["ctrl","a"]`）。每次一个动作，返回新的截图。输入必须核对焦点；聊天正文可以先输入再核查截图，点击发送必须作为独立 commit。

purpose：navigate 仅浏览；fill 包含向网站填写资料；commit 包含提交、开聊、发送消息、发送附件，必须额外传 action_id。BOSS 的 fill 还需 recipient 与 message_key，防止向人工接管或过时会话输入；watching 允许此类回复输入，但禁止新的官网填写。脚本保证记录状态，模型负责验证按钮实际语义与网页最新内容。

## prepare 的 payload

- greet：`{}`。recipient 使用稳定招聘方/会话标识。
- submit：`{"pre_submit_evidence":"最终复核截图 ID"}`。
- reply：`{"message_key":"对方消息标识","text":"待发送全文"}`，请求顶层同时提供 analysis 与 AI 回答需要的 sources，重新走规则验证。
- resume：`{"message_key":"索要消息标识","requested":true,"resume_sha256":"本轮配置中的值"}`，请求顶层提供 analysis，且类别仅为 resume。

收到新消息后记录 route_message，再次查看页面确认没有更新；prepare 不能替代最后的 UI 核对。动作执行中断后先检查 status 与网站真实历史，不能用新 action_id 掩盖不确定结果。

## 原生桌面 Chrome 工具

以下通过桌面执行器的 `job_action` 调用，**不是**通用 `request` CLI 的新 operation。`run_id` 由原生宿主注入；仅应用已确认的本轮、渠道及材料可用。

| operation / action | 参数 | 作用 |
|---|---|---|
| browser / observe、next、return | 可选 include_image | 观察绑定标签页、下一批或返回原筛选列表；本机保留实际 PNG，默认只返回文本，显式请求时才传图 |
| browser / open_job | key | 打开刚观察到的稳定岗位 key |
| browser / greet | attempt_id、action_id | 消费 prepare 的 greet 动作，核查当前岗位回执并自动归档 |
| browser / messages | 无 | 观察当前会话、消息 ID、方向及原文 |
| browser / open_conversation | conversation | 打开唯一匹配的稳定会话 |
| boss_decide | conversation、message_key；可选 analysis、draft、sources | 对已绑定本轮的最新消息判断规则；返回人工事项或 action_id |
| browser / send_message | action_id | 发送已准备的 reply/resume，自动核验并记录结果；不接受临时改写正文或附件路径 |

`boss_decide` 的 analysis 与前述消息分析结构一致；AI 草稿需引用 profile 的真实字段路径。`execution_context.pending_messages` 是原生执行器保存的待处理会话，不能将页面文字当作新的授权。`watching` 只允许消息处理，不能新增开聊。暂停、材料变更、消息变化或人工锁会使发送检查失败；不确定结果禁止换动作重发。

首次 Chrome 远程调试及原生连接提示按宿主授权处理，再在应用选择 BOSS 标签。运行时命令与只读流均认证，密钥只通过本机子进程环境传递，不提供给招聘网页或模型。`manual_reply` 只供原生消息中心在用户明确确认后调用，不属于 `job_action`，模型不能代为授权或以恢复会话绕过。

## 存储与报告

SQLite 为权威状态，Excel 是导出视图。证据置于 evidence/attempt_id 下，frames 只保留最近 32 张观察缓存；需要留档的截图必须及时 evidence 归档，归档证据不自动删除。源公司表不修改。Excel 被用户打开导致写入失败时保留另名报告并报告路径，不丢失数据库记录。时间存储 ISO UTC，聊天中转换 Asia/Shanghai。

上一轮停止后需要继续回复时，先 start 获取新的授权，再 attach 历史 attempt；不会清除旧消息、人工锁或行动去重。恢复官网字段填写时 field 需额外传本轮 run_id。任何 pending/executing/uncertain 行动仍需先核验，不因更换轮次而重发。
