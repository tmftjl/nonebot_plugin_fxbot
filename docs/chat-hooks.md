# 聊天生命周期扩展

会话 ID 与隔离策略见 [chat-session-hooks.md](chat-session-hooks.md)。其他扩展统一通过 `chat/hooks.py` 注册，核心不识别业务插件名称。

```python
from ...chat.hooks import PersonaOverride, register_chat_hook


def persona(context, current_name, current_text):
    if is_my_session(context.session_id):
        return PersonaOverride(name="运维", text="你是当前服务器的运维助手。")
    return None


def restrict_tools(context, details):
    if details["tool_name"] == "my_sensitive_tool" and not is_my_session(context.session_id):
        return False
    return None


remove_persona = register_chat_hook("persona", "my_plugin", persona)
remove_guard = register_chat_hook("tool_guard", "my_plugin", restrict_tools)
```

## 可用扩展点

| 类型 | 回调参数 | 返回值及执行位置 |
|---|---|---|
| `request_guard` | context, details | True/None 不干预；False 拒绝。普通 Planner、回复器生成前及 Focus 共享规划请求前检查 |
| `persona` | context, current_name, current_text | None 或 PersonaOverride。生成本次人格前替换，不改配置、绑定或真实聊天场景 |
| `prompt` | context | None 或字符串。追加独立 user 参考，不提供可改写的聊天历史或工具协议 |
| `tool_guard` | context, details | True/None 不干预；False 拒绝。工具候选构建、tool_search 和执行入口均检查 |
| `reply_text` | context, text | None 保持，字符串替换；空串抑制正文。在断句、生成持久化之前执行，不处理 Planner 的思考或工具参数 |
| `send_guard` | context, details | True/None 不干预；False 拒绝。逐段正文、附图、表情、语音及错字纠正发送前检查 |
| `observer` | event, context, details | 返回值忽略。审计和资源清理通知，不决定流程放行 |

`persona`、`prompt`、`reply_text` 必须同步，按注册顺序依次应用；禁止进行网络请求或其他阻塞操作。其他回调支持同步与异步，异步回调单次超时为 5 秒。需要长任务时由插件自行调度，并管理取消与关闭。

每种类型按名称保持注册顺序，同名替换不改变优先级。注册返回注销函数，重复调用安全，旧函数不能注销新实现。插件在自己的停用/卸载流程调用它；核心不会推测插件开关。

## 上下文与参数

`ChatHookContext` 是不可变身份快照：`session_id`、`user_id`、`group_id`、`platform`、`turn_id`、`phase`、`epoch`。不传入可写的 Bot/Event/Runtime/ChatRequest。

- request_guard：details 含 `text`、`trigger_kind`。这是模型生成入口的守卫，不是入站消息接收或历史落库前的过滤器；不替代路由、成员资格或命令权限。
- tool_guard：`phase="tool_discovery"` 时只有 `tool_name`，身份尽量从实际 runtime 事件提取；`phase="tool"` 时含本次验证后的 `arguments` 和工具调用上下文。回调若只检查参数，应在实际执行阶段检查。缺失身份应由敏感工具拒绝。
- send_guard：details 的 `kind` 为 `text/image/emoji/voice/correction`；文本含 `text`，正文含 `part_index`，附图含 `index`。语音在合成前与发送前各检查一次。守卫不能修改正文或发送目标；正文变换使用 reply_text。
- 身份快照不等于权限证明：权限仍由核心及敏感工具检查真实事件。hook 只能缩小可用工具，不能越过原权限、发现规则、允许列表或工具内部防护。
- details 常见容器递归冻结，每个回调拿到独立快照，不能通过嵌套参数修改实际命令、输出或调用者身份。

## 通知

| event | 意义与 details |
|---|---|
| `request_rejected` | 生成请求被守卫拒绝 |
| `request_start` | 普通 Planner/回复器一次生成开始，不代表外层整个对话结束 |
| `request_complete` | 一次生成完成；text、epoch；不代表已发送 |
| `request_error` / `request_cancelled` | 普通生成失败或被取消，不对外泄露异常对象 |
| `tool_start` | tool_name、验证后的 arguments |
| `tool_complete` | tool_name、result；结果可能包含敏感内容，插件自行决定是否脱敏或记录 |
| `tool_error` / `tool_cancelled` | 工具运行失败或被取消；预期工具错误可带 code |
| `part_sent` | 正文某段发送成功且进入可见历史，含 text、part_index；不表示整条回复发送完毕 |
| `delivery_complete` | 正文发送链结束，含 sent_parts、failed_index、cancelled、error；包括失败和部分发送 |
| `session_clear_start` | 核心清空前，用于插件取消挂起确认、任务等资源 |
| `session_clear_complete` / `session_clear_error` | 清空流程完成或中断；完成包含内存 epoch，不保证数据库成功清空，数据库降级仍按原有日志语义处理 |

Focus 共享规划的 request_guard 会检查所有成员，任一拒绝阻止本次共享模型调用；目标聊天回复走完整普通回复器和发送链。共享规划本身不伪装成单个聊天生成事件，因此不发普通 request_start/complete 通知。

独立表情工具没有正文发送链，因此会经过 send_guard，但不发正文 part_sent/delivery_complete。通知只对应真实挂载点，不应用来推断所有平台消息都经过统一审计。

## 错误与安全边界

- 守卫拒绝优先，异常、超时和非法返回都拒绝；后续 hook 不能覆盖拒绝。
- 人格、提示、正文过滤器异常会中止本次生成，不退回到另一种人格或未脱敏正文。
- 通知异常只记日志，不能阻止其他订阅者和核心清理；外部任务取消不吞掉。
- 不开放任意改写 session/user/group、发送目的地、权限结果、原始历史、数据库事务或已发送结果的 hook。
- 不改变 A_Memorix 的会话记忆/公共知识检索语义，也不提供 shell 执行、命令确认或业务持久化。
