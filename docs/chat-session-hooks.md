# 会话扩展 hook

`chat/session_hooks.py` 为插件提供同步会话解析器和会话策略。核心不识别具体业务插件或命名空间标记。

## 会话解析

```python
from ...chat.session_hooks import register_session_resolver


def resolve(bot, event, base_session_id):
    # 从插件自己的状态判断是否覆盖；不要再次调用 session_id_for_event。
    if not mode_enabled(bot, event):
        return None
    namespace, kind, target = base_session_id.rsplit(":", 2)
    return f"{namespace}#isolated:{kind}:{target}"


unregister = register_session_resolver("my_plugin", resolve)
```

- 回调参数是实际 Bot、实际 Event 和核心生成的基础 session ID。
- 按注册顺序询问，首个非空结果生效；返回 `None` 或空串表示不接管。
- 只能修改命名空间，必须保留平台、群聊/私聊类型和真实目标 ID。
- 结果记录在事件对象上，同一事件、同一基础 session ID 不会因模式切换或插件重载重新归属。新事件重新读取插件状态。
- 回调必须同步；不得阻塞、做网络请求或递归解析。持久状态应由插件提前加载。
- 解析异常、无效结果、递归解析，或事件不支持保存内部属性时拒绝处理，不能静默退回普通会话。
- 相同名称重复注册替换实现且保持顺序；返回的注销函数只移除其对应实现，可重复调用。

模式切换命令本身仍属于首次解析时的会话；切换对后续新事件生效。需要初始化另一个会话时，应从基础 ID 明确派生目标，不能修改当前事件缓存。

## 隔离策略

```python
from ...chat.session_hooks import SessionPolicy, register_session_policy


def policy(session_id):
    namespace, _, _ = session_id.rsplit(":", 2)
    if namespace.endswith("#isolated"):
        return SessionPolicy(allow_focus=False, allow_sharing=False)
    return None


unregister_policy = register_session_policy("my_plugin", policy)
```

- `allow_focus=False` 禁止 Focus 接管该会话，包括直接注册入口。
- `allow_sharing=False` 禁止表达、黑话的双向跨会话共享，仍允许读取本会话；Focus 也不把它并入配置的共享组。
- `allow_debounce=False` 跳过该会话普通消息的防抖等待，创建、续发和等待期间均检查；不更改全局档位。
- `allow_typing=False` 跳过该会话分段和纠错消息的模拟打字；发送守卫、目标与历史写入不变。
- `allow_split=False` 保留回复段落，禁用规则和模型分段；普通聊天默认不变。
- `allow_typo=False` 该会话的回复不做错字模拟，也不会补纠错消息；正文按模型原样发送。
- `allow_rest_time=True` 该会话不受「休息时间不回复」的工作日和工作时间限制，仍尊重 AI 总开关；服务器运维会话用它放行。
- `direct_reply=True` 声明该会话走纯人格直聊，核心不再读取全局直聊配置；无限二代在自身配置里提供开关。
- 响应节奏默认均为 True；未声明策略的群聊和私聊保持原有等待。取消等待不跳过 Planner、模型或真实任务执行。
- 未注册策略或回调返回 `None` 时保留现有行为。`allow_*` 限制项取布尔交集，不能覆盖其他插件的拒绝；
  `allow_rest_time`、`direct_reply` 是放行/声明项，任一插件声明即生效。
- 策略每次使用重新计算，支持配置热更新；异常或返回值类型错误时，Focus 和共享均拒绝。
- 策略不是权限授权，也不是 shell 沙箱。敏感工具必须独立检查实际调用者、场景、插件开关与会话身份。

## 边界

历史、人格绑定、缓存等原本按 session ID 保存的数据，自然遵循解析后的 ID。开关持久化、人格播种、命令注册、执行确认、权限与审计由业务插件负责。

本扩展不修改 A_Memorix 的会话记忆与公共知识语义，不禁用长期记忆，不改变检索内核。它也不会自动终止普通聊天或派生会话中的在途任务；模式切换的生命周期清理由插件明确处理。
