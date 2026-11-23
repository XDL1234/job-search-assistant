# 文档索引与维护约定

## 当前状态

| 内容 | 状态 | 入口 |
|---|---|---|
| 早期求职工具与本机面板 | 历史记录 | [实施记录](implementation.md) |
| 桌面应用八页界面 | 已确认第一版视觉基准 | [界面与图册](design/app-ui-v1/README.md) |
| 桌面产品与架构 | 已记录确认选项与实现边界 | [设计说明](design/app-ui-v1/desktop-spec.md) |
| Codex 接入 | 已完成模拟数据协议探针 | [验证报告](design/app-ui-v1/codex-probe.md) |
| 桌面应用实现 | v1.1.0 Windows 正式版 | [安装说明](releases/v1.1.0.md)、[构建发布](desktop-packaging.md) |
| 旧浏览器底座迁移 | beta.5 本机迭代记录 | [beta.5 说明](releases/v0.3.0-beta.5.md)、[后台说明](background-browser.md) |
| JPEG 实时预览与低 token 执行 | 已实现；三种模式各一次真实开聊，完成五轮改造与回归 | [详细方案](design/jpeg-stream-agent-browser.md)、[迭代记录](superpowers/plans/2026-11-18-agent-browser-iterations.md) |
| 发布历史 | 桌面版本与早期工具归档 | [变更记录](../CHANGELOG.md) |

## 维护约定

1. 功能边界以设计说明为准；图中的示例文字不自动扩展范围。新增决定同步更新设计说明、计划和变更记录。
2. 计划仅在对应验收通过后勾选。报告注明日期、环境、成功/失败和未验证项，区分模拟页面验证与真实平台验证。
3. 界面图片使用虚构数据。保留提示词以便追溯；修改图稿时同步更新图册与说明，不能悄悄改变已确认行为。
4. `.verification/`、`dist/`、真实简历、个人资料、聊天记录、登录令牌和业务截图不提交。应用安装包按白名单收录运行资源，设计稿随源码维护。
5. 文档更新检查相对链接、图片引用、JSON 有效性和 Git 差异；运行代码有变化时执行对应测试与完整回归。提交前检查未跟踪文件，不直接把整个工作目录打包。
6. 发布桌面版时同步更新应用版本、安装包和变更记录。

## 常用验证

在仓库根目录执行：

```powershell
python -X utf8 -m unittest discover -s "./tests" -v
git diff --check
```

桌面 Node/Rust 检查与窗口验收命令见[开发说明](desktop-development.md)。Codex 探针及桌面测试原始产物位于开发机被忽略的验证目录；仓库仅维护摘要，不发布凭据或原始 stderr。

## 最近维护验证

2026-10-06 的设计归档验证曾通过 42 项单元测试与设计资产检查；后续桌面开发的测试结果、外部阻断及未验收范围独立记录在[开发说明](desktop-development.md)，不以旧报告代替新实现验收。
