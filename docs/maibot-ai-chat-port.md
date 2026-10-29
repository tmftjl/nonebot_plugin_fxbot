# MaiBot AI 对话能力移植清单

参考仓库：`D:/project/MaiBot`，commit `062d45cf8bd4272ac57d7ec03fcf405e5a458211`（GPL-3.0，允许复制迁移）。
目标：把 MaiBot 聊天链路中**不依赖外置服务**的能力全部移植到 FxBot，保留 FxBot 的租户、时区、适配器与配置边界。

## 1. 边界

需要外置服务的能力**保留接口、分阶段实现**（先抽象出 Provider / 协议，默认实现可以留空或复用现有配置）：

- 向量库、embedding、表达向量索引 → `EmbeddingProvider` + `VectorStore` 接口；
- 外部联网搜索 → `WebSearchProvider` 接口（当前 `chat.tools.web_search` 先收敛到该接口）；
- ASR/TTS/语音 → `SpeechProvider` 接口，消息接收侧预留语音转写入口。

仍然不做：

- MCP、Redis、插件市场、maim_message 平台桥接、遥测、web 导入、computer/桌面控制。

数据迁移：**不迁移旧数据**。新列、新表直接按新结构上线，不写回填脚本；旧表情记录不做状态修正。

## 2. 已对齐，不重复实现

persona 三段（人格 / 行为风格 / 表达风格）、备用表达风格、额外 Prompt、注意力漂移、视觉画质、错字与纠正引用、断句与后处理、打字节奏、消息防抖、Planner 与回复门、A_Memorix 长期记忆、群风格、黑话学习、表达学习与复审、表情发送、引用回复（基础）。

## 3. 缺口清单

### 3.1 表情包系统（P1）

MaiBot：`src/emoji_system/emoji_manager.py`、`emoji_cache_cleanup.py`、`src/config/official_configs.py` 的 `EmojiConfig`。
FxBot 现状：`chat/emoji.py` 只有手工入库、模糊匹配发送；`ChatEmoji` 只有 `enabled`；控制台只有素材页表格。没有采集、状态机、审核、注册上限与清理。

状态：**已实现**。后台任务含补识别（`check_interval`）与缓存清理（`cache_cleanup`）；控制台提供状态计数、格式筛选、哈希/描述/标签搜索、按加入/注册/最近使用/发送次数排序，以及注册、禁用、编辑描述、删除。未做：缩略图缓存、批量删除。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 状态机 | `is_registered` / `is_banned` / `vlm_processed` 推出 已注册 / 待审核已识别 / 待审核未识别 / 已禁用 | `ChatEmoji` 增加 `registered`、`vlm_processed`、`register_time`；`enabled=false` 即已禁用 |
| 从聊天采集 | 收到表情组件时按哈希缓存文件与记录（`steal_emoji`） | 适配器新增表情源提取；`chat/emoji.py` 落盘并写待审核记录 |
| 视觉识别 | VLM 生成描述 + 情绪标签，成功才可注册 | 复用 `chat/vision.py::describe_emoji`，后台任务 |
| 内容审核 | `content_filtration` 开启时 VLM 判断是否适合做表情 | 新增审核提示，失败留在待审核 |
| 注册上限 / 替换 | `max_reg_num`，满额且 `do_replace` 时 LLM 选一个旧表情取消注册 | 注册前检查数量；替换用文本模型决策 |
| 清理 | 未注册文件与无文件记录按天保留；已注册永不清理 | 定时任务接入现有 scheduler |
| 控制台画廊 | 状态计数、格式筛选、注册时间排序、描述/标签/哈希搜索、注册/禁用/删除/上传 | `/chat/emoji` 扩展列表筛选与统计；前端改成卡片画廊 |
| 发送候选 | `emoji_send_num` 控制候选池 | 候选上限配置化 |

### 3.2 图片与视觉（P1）

MaiBot：`VisualConfig`、`src/chat/image_system/`。
FxBot 现状：已有画质、数量、大小上限。

状态：**已实现**。识图等待只约束回复后写回图片描述的时间；过大图片按配置压缩到上限内或直接跳过；表情采集不压缩。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 识图等待 | `wait_image_recognize_max_time`，0 表示不等待 | `chat.visual.wait_image_recognize_max_time`，超时保留占位 |
| 过大图片 | `handle_oversized_images` + `compress\|discard` | 压缩模式下放宽下载硬上限后压到上限内；关闭或 discard 时跳过 |
| 图片缓存清理 | `image_cache_cleanup` 按天清理旧文件与无文件描述 | FxBot 图片不落盘（只存描述），无需清理任务 |

### 3.3 回复方式（P2）

MaiBot：`ChatReplyStyleConfig`、`ChatConfig`。

状态：**引用频率、群/私聊上下文、回复 @ 已实现**。`chat.behavior.group_quote_frequency` / `private_quote_frequency` 取代旧 `quote_mode`；每收到一条消息与每发出一段回复都记入会话计数器，发送首段前按频率抽签。配置页同步提供两个下拉框。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 引用频率 | 群聊 / 私聊各自 `never|occasionally|often`，按连发、间隔、目标位置用计数器决定 | 移植 `maisaka/quote/counter.py` 到 `chat/behavior/quote.py` |
| 群/私聊上下文 | `max_context_size` 与 `max_private_context_size` 分开 | 已实现：`chat.max_context_size`（群聊，默认 30）与 `chat.max_private_context_size`（私聊，默认 60） |
| 回复 @ 用户 | `enable_reply_at` 允许模型在开头 @ 人 | 已实现：`chat.behavior.enable_reply_at`；解析首段开头的 `@昵称`，只解析本条消息发送者与被 @ 成员，构造平台 at 段 |
| 昵称 / 别名 | `nickname`、`alias_names` 参与提及识别 | 已实现：`chat.behavior.persona_aliases`；群消息以别名开头时免 @ 触发，并写入人格提示 |

### 3.4 消息接收（P2）

MaiBot：`KeywordReactionConfig`。

状态：**已实现**。`chat.message_receive.keyword_rules` / `regex_rules`；正则支持 `(?P<name>...)` 命名组与 reaction 里的 `[name]` 替换，命中内容以【关键词反应】块注入本轮人格提示。控制台「消息接收」卡片提供关键词与正则规则编辑器。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 关键词 / 正则反应 | 命中后追加固定 reaction 提示，不直接发送 | 新配置 `chat.keyword_reaction`；命中内容并入本轮 prompt |

### 3.5 学习配置（P2）

MaiBot：`ExpressionConfig`、`JargonConfig` 的 `learning_list` / `*_groups`。

状态：**已实现**。`chat.expression.learning_list` / `chat.jargon.learning_list` 按 `platform + item_id + group|private` 匹配，越具体越优先；`*` 与留空都表示该层级默认值。`groups` 只在检索时合并共享租户，学习仍写入各自群。表达新增 `checked_only`（只注入控制台人工确认项）与 `self_reflect`（关闭后跳过写入前 AI 复审）。`learning_list` / `groups` 是对象数组，控制台「学习」分类下提供条目编辑器。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 分聊天 use / learn | 可指定某群只使用不学习，或只学习不使用 | `learning_list` 的 `use` 控制注入、`learn` 控制学习；目标从租户解析 |
| 共享组 | 多个群共享表达 / 黑话 | `groups: [{targets: [...]}]`；注入时把同组已知租户合并，支持 `*` 通配 |
| 仅精选表达 | `expression_checked_only` 只用人工确认项 | `chat.expression.checked_only`；只保留 `manual_checked=true`（控制台改判写入） |
| 表达自省 | 写入前 AI 复审 | `chat.expression.self_reflect`；关闭后学习结果直接作为可用表达 |

### 3.6 上下文与工具（P3）

MaiBot：`src/maisaka/context/compaction.py`、`tool_result_condenser.py`、`tool_result_store.py`。

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 滚动状态摘要 | 超过条数后用 LLM 维护摘要，始终随 Planner 上下文发送 | 会话级摘要表 + 定期压缩 |
| 长工具结果整理 | 超过阈值先由子模型整理成结论 | 工具结果入上下文前整理 |
| 工具结果存储 | 超长结果落库，上下文只留引用 | 新增工具结果表与读取工具 |

### 3.7 Planner 进阶（P3）

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 快速反应 / steering | 流式规划，新消息到达立即打断重规划 | 规划请求支持取消与重发 |
| 连续等待提醒 | 连续 wait 时追加提醒 | 规划轮次状态 |
| 子代理 | Planner 把独立任务交给子代理；可后台运行 | 只依赖 LLM 与现有工具，按 P3 评估 |

### 3.8 实验能力（P4）

| 功能 | MaiBot 行为 | 落地 |
|---|---|---|
| 被动式 replyer | Planner 直接输出正文时启动一次回复 | 评估后实现 |
| Focus 模式 | 同组共享持续思考的 Planner | 架构改动大，放最后 |
| retro / document replyer | 特定回复形态 | 评估是否与 FxBot 发送链路冲突 |

### 3.9 外置能力接口（先留接口）

这些能力允许依赖外置服务，但 FxBot 侧先固定边界，默认关闭，不接入具体厂商。

| 接口 | 现状 | 计划 |
|---|---|---|
| `EmbeddingProvider` | `chat/providers/base.py` 已有抽象与 `embed` / `embed_single` | 补 manager 路由、控制台配置与真实调用点 |
| 表达向量索引 | MaiBot `expression_vector_index.json` + 嵌入 | 新增本地 JSON 索引 `chat/memory/vector.py`，嵌入走 `EmbeddingProvider`，默认关闭 |
| `TTSProvider` / `STTProvider` | 抽象已存在，未接入 | 消息接收预留 voice 段 → STT；发送侧预留 TTS，默认关闭 |
| `WebSearchProvider` | `chat/tools/web_search.py` 直接 HTTP + `chat.web_search` 配置 | 收敛为 Provider 接口，按配置选择实例 |
| 向量库 | 无 | 只留 `VectorStore` 协议（add / search / delete），默认实现为本地 JSON，不引入外部数据库 |

判断标准：接口、配置键、调用边界先落地；具体厂商实现可以后补，未配置时功能必须安全降级。

## 4. 数据结构

`chat_emoji` 新增：

- `registered`、`vlm_processed`、`register_time`；
- 旧 `enabled` 继续表示是否禁用；
- 不迁移旧数据，旧记录按新默认值呈现。

`chat_expression` 新增 `manual_checked`：只有控制台人工改判才置真；`checked_only` 用它区分 AI 复审与人工确认。

新增表（P3）：

- `chat_context_summary`：租户 / 会话摘要 / 覆盖到的消息序号 / 更新时间；
- `chat_tool_result`：租户 / 调用 ID / 工具名 / 完整结果 / 创建时间。

## 5. 配置

新增或补齐（全部放在 AI 对话相关标签下）：

- `chat.emoji`：`steal_emoji`、`max_reg_num`、`do_replace`、`emoji_send_num`、`max_emoji_size_mb`、`content_filtration`、`cache_cleanup.*`；
- `chat.visual`：`wait_image_recognize_max_time`、`handle_oversized_images`、`oversized_image_handle_method`、`image_cache_cleanup.*`；
- `chat.reply_timing` / `chat.behavior`：群/私聊引用频率、群/私聊上下文条数、`enable_reply_at`、昵称与别名；
- `chat.keyword_reaction`：关键词与正则规则；
- `chat.typo`：`min_freq`（默认 9，候选字频低于该值不造错字）；
- `chat.expression` / `chat.jargon`：`checked_only`、`self_reflect`、`learning_list`、`groups`；
- `chat.planner`：`message_steering`、`context_summary_max_chars`；
- `chat.capabilities`：`tool_result_condense`、阈值、子代理开关；
- `chat.providers`：新增 `embedding`、`tts`、`stt` 路由键与对应 Provider 配置；
- `chat.expression`：`vector.enabled`、`vector.index_path`、`vector.candidate_pool_size`（默认关闭）；
- `chat.voice`：`enabled`、`stt_provider`、`tts_enabled`、`tts_provider`；
- `chat.web_search`：改为 `provider` + 各 Provider 配置，保留现有 `base_url` / `api_key`。

配置对齐状态（2026-10-28）：`chat.typo.min_freq` 已落地。其余缺口依赖对应子系统，未提前加空配置：

- `visual.image_cache_cleanup.*`：FxBot 图片不落盘，无清理对象（见 3.2）；
- `emoji.use_new_send_logic`：依赖 `emoji` 模型任务与选图工具，随表情发送逻辑一起做；
- `context_compaction_summary_max_chars`：依赖 `utils` 模型任务与 Maisaka 上下文压缩（P3）；
- `reply_timing.planner_message_steering`：依赖流式 Planner（P3）；
- `reply_timing.private_talk_value`：MaiBot 标注仅兼容、不再生效，不搬；
- 表达向量四项：依赖嵌入路由与 `expression_vector_index`（P2/P3）。

## 6. 实现顺序

1. **P1 表情包**：模型与仓库 → 适配器表情源 → 采集与后台识别/审核 → 注册上限与替换 → 清理 → 控制台 API 与画廊 → 配置与文档。
2. **P1 视觉**：识图等待、过大图片压缩/丢弃、图片缓存清理。
3. **P2 回复方式**：引用频率计数器、群/私聊上下文、回复 @、昵称别名。
4. **P2 消息接收**：关键词/正则 reaction。
5. **P2 学习配置**：分聊天 use/learn、共享组、仅精选、自省开关。
6. **P3 上下文与工具**：滚动摘要、长工具结果整理、工具结果存储。
7. **P3 Planner**：steering、等待提醒、子代理。
8. **P4 实验**：被动 replyer、Focus、retro/document。

每一批独立测试通过后再进入下一批；每批完成后更新本文件的状态。

## 7. 验证

- 表情采集：同图去重、跨租户隔离、超大跳过、未配置视觉模型时保持待审核未识别；
- 注册审核：内容过滤拒绝、注册上限触发替换、禁用后不再发送；
- 清理：未注册过期删除、已注册永不删除；
- 控制台：状态计数、筛选、排序、搜索、注册/禁用/删除/上传；
- 视觉：超限压缩与丢弃、识图等待超时；
- 引用：群/私聊频率与计数器分布；
- 学习：分聊天 use/learn 与共享组只影响目标租户；
- 上下文：摘要压缩后仍能继续对话。

## 8. 长期记忆改为 A_Memorix

MaiBot 的长期记忆内核已原样内置到 `chat/amemorix/`（v2.0.0，GPL-3.0），FxBot 只保留宿主适配层：

- 写入：`chat/amemorix/writeback.py` 读取 `chat_message`，按租户批量写回；
- 读取：`chat/service.py::_memory_block` 调用 `chat/amemorix/inject.py`；
- 人物：`chat/amemorix/people.py` 的 `ChatPerson` 按租户隔离；
- 工具：`query_memory`、`query_person_profile`；
- 控制台：`/amemorix/*` 与「长期记忆」页。

旧 `chat/memory` 抽取、事实、画像、Episode、关系、别名、知识库和 `chat.knowledge` 配置已删除；旧表不再迁移、不再读写，数据留在原库中，确认无用后可手动清理。`chat/memory` 现在只保留历史存储、群风格、黑话、表达、表情、行为与轮次追踪。
