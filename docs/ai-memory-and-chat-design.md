# FxBot AI 对话源码核查入口

原分阶段设计稿已移除，其中的代码现状、旧记忆表、阶段计划和部分隔离约束已不符合实现。本文只提供核查入口，不是实施规范或待办计划；行为以当前源码和测试为准。

## 核查入口

- 消息准入、主动接话与 Focus 分支：`chat/router.py`；回复调用链：`chat/service.py`。
- 配置：对照 `config/system_defaults.py`、`console/routes/config.py`、校验及业务读取位置，分别确认默认值、界面字段和生效条件。默认值不是实例保存的值。
- 普通调度：`chat/behavior/turn_scheduler.py`；普通规划循环：`chat/heart_flow/conversation.py`、`chat/heart_flow/plan_flow.py`；Focus：`chat/focus/`。两者不能当作同一路径。
- 历史与轮次：`chat/session.py`、`chat/memory/models.py`、`chat/memory/repository.py`；消息接收与发送：`chat/message_receive/`、`chat/behavior/`。
- 长期记忆：`chat/amemorix/` 及其 `_host/` 适配层。`chat/memory/` 仍负责聊天持久化与学习等工作，不等同于旧事实抽取管线。
- 共享范围：分别检查 Focus、表达/黑话共享组及 A_Memorix 检索配置。会话归属、共享读取范围和工具授权是不同概念。

## 旧稿已确认失真的内容

- 历史已有持久化与恢复路径，不能再称为“只在内存、重启即失忆”。
- 防抖、拆分、表情包、长期记忆和主动接话已有实现；是否生效须检查实际配置，不能继续按旧阶段判定缺失。
- 旧稿的 30 秒防抖上限已失效；核查时 `chat/behavior/debounce.py` 的 `BATCH_LIMIT` 为 8 秒，具体等待还受档位、提及和条数影响。
- “所有数据永不跨聊天共享”与显式共享功能冲突，应防止的是绕过配置或授权的隐式串用。
- 旧记忆表和 BM25 抽取方案不能作为当前 A_Memorix 的实现说明。

## MaiBot 对照方法

使用指定的本地 `temp/MaiBot` 源码，不将其视为远端最新版。分别比较字段、默认值、生效条件及调用流程，尤其核查普通 Planner、快速反应、被动式 replyer、工具权限和共享范围。同名不代表等价；模型、人设、提示词和实例配置也影响实际聊天表现。
