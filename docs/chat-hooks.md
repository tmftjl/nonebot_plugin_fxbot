# 聊天生命周期扩展

业务插件统一从 `chat/extensions.py` 导入扩展接口；会话 ID 与隔离策略见
[chat-session-hooks.md](chat-session-hooks.md)。已有 `chat/hooks.py` 和 `chat/session_hooks.py` 注册函数继续可用，
底层共用同一套优先级、注册快照、替换和精确注销机制。核心不识别业务插件名称。

## 统一注册与插件生命周期

```python
from ...chat.extensions import ChatExtension, SystemPromptSection

extension = ChatExtension("my_plugin")


def system_prompt(context):
    if context.phase == "reply":
        return SystemPromptSection("插件提供的系统段")
    return None


def stage_guard(context, details):
    # 业务判断及开关读取属于插件；守卫只能收紧，不授予权限。
    if details["stage"] == "learning_write" and not learning_allowed(context.session_id):
        return False
    return None


extension.on("system_prompt", system_prompt)
extension.on("stage_guard", stage_guard, priority=50)
# 插件停用/卸载时调用；不是在每轮对话结束后调用。
# extension.close()
```

`on(kind, callback, key="default", priority=100)` 支持本文所有 Hook，以及 `session_resolver`、`session_policy`。
同一插件、同类型、同 key 替换实现；不同 key 可注册多个回调。优先级必须是整数，越小越先执行，
相同优先级按注册顺序执行，同名替换保留位置。现有注册函数也接受关键字 `priority`。
系统提示词最终段落顺序仍由 `SystemPromptSection.order` 和注册名决定，不受回调执行优先级取代。

注册返回单项注销函数；`close()` 一次性注销本实例的全部注册、撤销其续话任务，可重复调用。
热重载时旧实例的注销函数不能删除新注册，即使两次注册传入同一个函数对象；旧实例关闭也不会取消新实例的任务。
每次派发固定订阅者快照，回调期间注册/注销只影响下一次派发。插件自己的开关、配置注册和 UI schema 仍由插件负责。
有序订阅者快照只在注册/替换/注销时重建，派发不重新排序；同一插件重复替换注册不会累积旧注销句柄。
`HookKind` 和 `StageName` 是公开的 Literal 类型，可供插件的类型检查与补全使用。

### 后台审计与事件筛选

默认通知仍原地执行，保留资源清理和对话时序。纯审计可显式启用后台模式：

```python
extension.on(
    "observer",
    audit_callback,
    events={"delivery_complete", "stage_complete"},
    background=True,
    queue_size=128,
)
```

`events` 仅对 observer 有效，必须是事件名称集合；None 接收全部，空集合不接收任何通知。
筛选在参数快照准备前执行，不匹配时不复制大载荷。底层 `register_chat_hook` 也支持 events 筛选，
后台模式由 `ChatExtension` 管理。守卫和过滤器不能后台执行。

后台审计每个订阅使用独立有界 FIFO，queue_size 为 1 到 4096，默认 128，另有最多一个在执行的回调。
调用方只负责入队，不等待审计完成；不同订阅之间没有完成顺序保证。队满丢弃最新通知，日志限频报告累计丢弃量。
回调异常/超时不阻止后续通知；空闲时 worker 退出，不为每个订阅常驻空等任务。
同实例替换注册、注销、插件 close 会取消对应 worker 和积压通知；热重载仍需关闭旧实例。
框架退出时在共享资源释放前等待取消完成，不排空积压队列。
因此它是尽力审计，不是可靠消息队列，也不能用于权限、确认流程、会话清空或必须即时完成的资源清理。

后台回调只收到身份快照，不继承入站 Bot、权限或轮次 ContextVar。网络/文件 I/O 应使用异步回调；
后台队列不等于线程池，同步阻塞函数仍会阻塞事件循环，五秒超时也不能抢占同步阻塞代码。

性能微基准可在与测试相同的解释器和 PYTHONPATH 环境运行 `python -m tests.bench_chat_hooks`。
它对比旧算法与当前实现的本地注册读取和通知派发，不代表模型请求或完整对话的加速倍数。

## 流程覆盖与阶段边界

| 边界 | 正式接口 | 拒绝语义 |
|---|---|---|
| 会话解析与策略 | `session_resolver` / `session_policy` | 保留原会话隔离契约 |
| 入站匹配 | `ingress_guard` | 不接收这一路 AI 消息，不落该路历史、不交给 Focus |
| 整次生成 | `request_guard` | 不开始该次生成 |
| 人格与提示组装 | `persona` / `system_prompt` / `prompt` | 明确返回值；错误中止生成 |
| 记忆检索、学习参考 | `stage_guard` | 本次不注入被拒绝的参考 |
| 普通/被动/Focus 规划、回复模型 | `stage_guard` | 不调用该阶段模型，返回空响应 |
| 工具发现与执行 | `tool_guard` | 不能发现或不能执行 |
| 回复正文处理 | `reply_text` | 替换或抑制正文，在断句与生成持久化前执行 |
| 表达、黑话学习写入 | `stage_guard` | 整次写入不执行，游标和计数不推进 |
| 逐条实际发送 | `send_guard` | 不发送该条输出；不伪造已发送历史 |
| 清空、错误、取消、发送结果 | `observer` | 只观察，不改执行结果 |

`ingress_guard(context, details)` 支持同步/异步，`details.path` 为 `reply` 或 `context`，含原始廉价提取的 `text`。
它在核心匹配、白名单、开关和过滤通过后运行，不能把原本不匹配的消息强行放行，不能恢复被禁用的 AI。
身份快照的 phase 为 `ingress`，没有 turn_id/epoch；此时不能安排续话。
随后通知 `ingress_accepted` / `ingress_rejected`，表示路由匹配结果，不保证 handler 已执行或历史已落库。
普通私聊、@/人格名回复与普通上下文路由在稳定配置下互斥，不是每条消息固定调用两遍。
同一事件、同一 Bot、同一解析会话只执行一次守卫并通知一次，首次命中的 path 决定本事件的语义；
规则重试、并发询问或两路之间的配置变化复用首次结果，不能重复扣额度或翻转拒绝。
新事件重新判断。首次判断被取消时同事件不会重试放行；取消等待结果的重复询问者不会取消首次判断。
守卫不得递归询问同一事件。platform 直接读取真实 Bot 适配器，不从会话命名空间反推。
它不替代框架级成员门禁、命令权限，也不拦截其他插件的命令。

`stage_guard(context, details)` 使用同一守卫返回值和超时规则，按 `details["stage"]` 选择阶段：

| stage | 位置与 details |
|---|---|
| `memory_retrieval` | A_Memorix 聊天参考读取前，含 query；不是独立工具的检索入口 |
| `learning_reference` | 普通、Focus 的表达/黑话参考组装前，含 query |
| `planner` | 每次普通/被动规划或 Focus 共享规划前；共享规划使用组级身份，不借用成员身份 |
| `reply` | 普通、Focus、直聊的回复模型生成前，纯推理续写属于同一次阶段 |
| `learning_write` | 自动表达、黑话批次及释义写入前，phase 为 learning，身份仅有真实 session_id |
| `scheduled_reply` | 插件安排的后续回复开始前，含 owner、key；之后仍经过 request/reply/send 守卫 |

阶段发出 `stage_start`、`stage_complete`；拒绝发 `stage_rejected`，不发 start/complete；
执行失败或取消发 `stage_error` / `stage_cancelled`，保留原核心异常与取消传播。
所有阶段通知含 stage；start/rejected/complete 带原 details 的独立快照，complete 另有 result。
result 是阶段返回结果，不等于模型输出已经发送，也不等于学习成功；学习成功必须同时检查 `result is True`。
`result`、`event`、`hook_kind` 是阶段详情的保留字段，调用方传入时在守卫和操作执行前报参数错误，
不能等操作已经完成后才因通知字段冲突失败。
模型结果、学习候选和检索文本可能敏感，审计插件必须自行选择记录范围与脱敏策略。

学习写入的 `kind` 为 `expression`、`jargon`、`jargon_inference`。
表达含 new_items、updates、updated_items、cursor_seq，其中 updated_items 是被更新已有表达的内容快照；
黑话含 candidates、cursor_seq；释义含 item_id、meaning、accepted。
守卫在仓储事务外执行，只能拒绝整次写入，不能修改候选、事务、证据或游标；
拒绝后保留素材，沿用现有学习重试机制，不承诺开关一变就立即重试。
人工控制台编辑、维护迁移、使用计数、A_Memorix 独立内核写入不是此自动学习阶段，不能据此宣称全库写入审计。

阶段守卫用于稳定业务边界，不给每一行内部函数都挂回调，不开放历史事务、权限结果或发送事实改写。
未来在这些边界内新增业务只需写插件；新增平台能力、协议或未覆盖的存储系统仍可能需要核心扩展。

## 插件发起后续对话

`extension.schedule_reply(context, instruction, delay=0, key="default")` 返回 `asyncio.Task[str]`，
正常完成时返回已发送正文，失效或未发送时可返回空串，取消继续传播；
部分发送后失效也可能返回空串，真实交付以 part_sent / delivery_complete 通知为准。
这里只提供基础能力，不自动安排任何续话业务。

应从普通聊天或 Focus 目标回复的 `request_complete` / `delivery_complete` 通知中取得真实上下文；
源自后台续话的快照 `source` 为插件名，真实消息为 `message`，插件可据此避免反馈循环。
需要只在发送成功后安排时，检查 delivery_complete 的 sent_parts，而不是 request_complete 的生成结果。
全局 Focus 共享规划快照、任意拼造的会话 ID、学习和 ingress 快照都不能用来建立发送目标。

核心捕获真实轮次的 Bot、平台目标、会话和 epoch，插件不能提供或覆盖目标。
后续轮不伪造用户消息，不继承原事件或用户管理员身份，使用会话人格和可见历史，经正式回复器与发送链发送。
核心为它设置明确的 continuation 标记：不构造本轮用户占位或用户学习条目，
本轮输入是明确标注“没有新的用户消息”的续话任务，继续使用真实历史及正常的人格、记忆、插件参考。
仍记录主动回复轮次和助手生成/发送结果，但其触发用户和触发消息 ID 列表为空，不写 ChatMessage 或用户 ChatTurnItem。
续话运行时的工具允许集合为空，发现和执行都拒绝工具；回复模型仍不携带工具声明，不进入 Planner 工具循环。
它不是任意消息投递接口，也不执行用户授权的工具。AI 关闭、作息不允许、纯人格直聊、Bot 离线、
群级主动接话显式关闭或会员失效时不执行；
生成前及每次实际发送前重新检查连接、会话开关、群/私聊白名单和会员，不使用原管理员的会员豁免。
目标处于有效 Focus 共享组时，仅把实际已发出的正文逐段同步到该组时间线，包含部分发送，
不会伪造共享组用户输入或主动唤醒共享规划；相关旧规划快照失效。

同实例、同会话、同 key 的任务替换旧任务；不同插件互不覆盖。
新消息、更新的目标轮次、会话清空、插件 close、框架关闭或目标过期后旧任务失效。
单次延迟最长一小时，真实目标最多保留一小时且最多 1024 个，任务全局最多 256 个。
任务仅驻留内存，重启不恢复、不自动重放；需要业务持久化的插件自行保存意图，不能恢复旧 Bot/权限凭证。

## 纯人格直聊的边界

纯人格直聊由插件通过会话策略 `direct_reply` 声明（当前由无限二代在自身控制台配置中提供开关），默认关闭。
开启后，回复模型仅收到当前会话绑定人格（未绑定则用全局人格）的文件原文作为 system、显式支持直聊的插件系统段、
当前会话的可见短期对话历史，以及当前消息作为 user；不传引用内容、长期记忆、时间/场景规则、工具或 Planner/Focus 指令。
无限二代提示词开关开启时，其原文用空行拼接在人格后，位于同一条 system 消息中，关闭时不添加。
图片直接传给回复模型，需要其支持多模态；语音仍先按现有接收配置转写。
此模式仅以 `phase="direct_reply"` 调用 `system_prompt`，贡献者应显式判断阶段；不调用 `persona/prompt/reply_text` 过滤器，
但保留 `request_guard`（phase 为 `reply`）、`send_guard`、生命周期通知、权限和会话隔离校验。
正文不经分段/错字/提及转换，原样发一条文字，不补发语音、图片或表情，也不投递本轮的记忆学习任务。
短期历史沿用“群聊上下文 / 私聊上下文”条数，设为 0 时不保存也不传历史。
仅传真实用户消息及已发送的助手正文，不传内部规划、工具调用、参考段或历史元数据；当前消息不重复插入。
群聊仅响应现有的 @/人格名触发，不主动插话。关闭开关即可恢复正常链路；开关按轮次读取，
不会改写已经发出的模型请求，也不会取消其他来源已经排队的后台任务。

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
| `ingress_guard` | context, details | 核心路由通过后、入站历史和 Focus 接管前的拒绝型守卫 |
| `stage_guard` | context, details | 稳定阶段执行前的拒绝型守卫，按 details.stage 选择阶段 |
| `request_guard` | context, details | True/None 不干预；False 拒绝。普通 Planner、回复器生成前及 Focus 共享规划请求前检查 |
| `persona` | context, current_name, current_text | None 或 PersonaOverride。生成本次人格前替换，不改配置、绑定或真实聊天场景 |
| `system_prompt` | context | None 或 SystemPromptSection(text, order=100)。人格解析完成后、阶段任务规则之前插入独立系统段 |
| `prompt` | context | None 或字符串。追加独立 user 参考，不提供可改写的聊天历史或工具协议 |
| `tool_guard` | context, details | True/None 不干预；False 拒绝。工具候选构建、tool_search 和执行入口均检查 |
| `reply_text` | context, text | None 保持，字符串替换；空串抑制正文。在断句、生成持久化之前执行，不处理 Planner 的思考或工具参数 |
| `send_guard` | context, details | True/None 不干预；False 拒绝。逐段正文、附图、表情、语音及错字纠正发送前检查 |
| `observer` | event, context, details | 返回值忽略。审计和资源清理通知，不决定流程放行 |

`persona`、`system_prompt`、`prompt`、`reply_text` 必须同步；禁止进行网络请求或其他阻塞操作。系统段按有限数值 `order` 升序、再按注册名排序，用空行拼接；其余过滤器按优先级和注册顺序依次应用。其他回调支持同步与异步，异步回调单次超时为 5 秒。需要长任务时由插件自行调度，并管理取消与关闭。

`system_prompt` 接收只读身份快照，不能修改原始人格、历史或工具声明。普通规划（含被动规划）的 phase 为 `planner`，回复为 `reply`。Focus 共享规划以 `phase="focus_planner"`、`session_id=共享组 scope_id` 的组级快照组装一次，用户、群组、平台和轮次字段留空，不借用任一成员身份；各聊天的 user 参考中不重复插入系统段。此扩展不接入独立子代理等非聊天模型调用。开关在组装时读取，不会追溯修改已经组装好的请求。

每种类型按优先级和注册顺序派发，同名替换保留注册位置，显式传入的新 priority 会更新优先级。
注册返回注销函数，重复调用安全，旧函数不能注销新实现。插件在自己的停用/卸载流程调用它；核心不会推测插件开关。

## 上下文与参数

`ChatHookContext` 是不可变身份快照：`session_id`、`user_id`、`group_id`、`platform`、`turn_id`、`phase`、`epoch`、`source`。不传入可写的 Bot/Event/Runtime/ChatRequest。

- request_guard：details 含 `text`、`trigger_kind`。这是模型生成入口的守卫，不是入站消息接收或历史落库前的过滤器；不替代路由、成员资格或命令权限。
- tool_guard：`phase="tool_discovery"` 时只有 `tool_name`，身份尽量从实际 runtime 事件提取；`phase="tool"` 时含本次验证后的 `arguments` 和工具调用上下文。回调若只检查参数，应在实际执行阶段检查。缺失身份应由敏感工具拒绝。
- send_guard：details 的 `kind` 为 `text/image/emoji/voice/correction`；文本含 `text`，正文含 `part_index`，附图含 `index`。语音在合成前与发送前各检查一次。守卫不能修改正文或发送目标；正文变换使用 reply_text。
- 身份快照不等于权限证明：权限仍由核心及敏感工具检查真实事件。hook 只能缩小可用工具，不能越过原权限、发现规则、允许列表或工具内部防护。
- details 常见容器递归冻结，完全由不可变值组成的载荷在同次派发内复用只读快照；
  含自定义对象时仍逐回调独立深拷贝，不能通过嵌套参数修改实际命令、输出或调用者身份。

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
- 人格、系统段、提示、正文过滤器异常会中止本次生成，不退回到另一种人格或未脱敏正文。
- 通知异常只记日志，不能阻止其他订阅者和核心清理；外部任务取消不吞掉。
- 不开放任意改写 session/user/group、发送目的地、权限结果、原始历史、数据库事务或已发送结果的 hook。
- 不改变 A_Memorix 的会话记忆/公共知识检索语义，也不提供 shell 执行、命令确认或业务持久化。
