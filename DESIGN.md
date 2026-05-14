# nonebot-plugin-fxbot 设计

参考 `temp/` 中的原项目核心模式，重新实现 `nonebot-plugin-fxbot`。

**`temp/` 是参考代码目录。开发时只在当前 `nonebot_plugin_fxbot/` 项目内实现，运行时代码不得依赖或导入 `temp/`。**

**db/ 和 chat/providers/ 第一版按 `temp/` 中对应实现适配，只改导入路径和必要命名，保留原复杂逻辑。**

---

## 一、功能清单

| 模块 | 说明 | 状态 |
|------|------|------|
| 权限系统 | 群/用户黑白名单，PermLevel 等级控制 | 核心 |
| 会员管理 | 续费码、到期提醒、门禁守卫 | 核心 |
| AI 对话 | 多 Provider，工具调用 | 核心 |
| Plugin 包装器 | 子插件注册命令，自动注入权限 | 核心 |
| AI 兜底路由 | 无命令匹配 → AI 回复 | 核心 |
| 子插件→工具 | `@tool` 装饰器注册 AI 可用工具 | 核心 |
| 控制台 | Web 管理面板 | 核心 |
| 记忆系统 | AI 上下文记忆，接口预留，轻量实现 | 后置 |

## 二、第一版不做

| 模块 | 处理 |
|------|------|
| 长期记忆 | 后置，先留接口 |
| RAG / 知识库 | 后置 |
| MCP | 后置 |
| 多数据库 | 第一版只用 SQLite |

---

## 三、目录结构

```
nonebot_plugin_fxbot/                   # 框架包，包含内置子插件
│
├── __init__.py                         # 插件入口
│   # __plugin_meta__
│   # @driver.on_startup → bootstrap.init()
│   # load_plugins("plugins/")
│
├── db/                                 # 数据库（参考 temp/db/base_models.py）
│   └── base.py                         # SQLModel 引擎初始化 + 基类
│
├── permission/                         # 权限系统
│   ├── types.py                        # PermLevel, PermScene, PermContext, Decision
│   ├── policy.py                       # PolicyChain 策略链
│   ├── storage.py                      # JSON 持久化
│   └── checker.py                      # PermissionChecker + permission_for()
│
├── plugin/
│   └── builder.py                      # Plugin 类（__getattr__ 动态代理）
│
├── membership/                         # 会员系统
│   ├── models.py                       # 三张表：MembershipGroup / RenewCode / RenewRecord
│   ├── service.py                      # 业务逻辑（新增、延期、生成码、兑换）
│   ├── guard.py                        # 门禁判定（缓存 + 豁免链）
│   ├── gate.py                         # event_preprocessor（必须最早导入）
│   ├── commands.py                     # 续费、查到期、控制台登录
│   └── tasks.py                        # 到期提醒、自动退群、缓存刷新
│
├── chat/                               # AI 对话
│   ├── types.py                        # ChatRequest, ChatResponse, InboundSegment
│   ├── service.py                      # ChatService 对话编排
│   ├── session.py                      # 会话 + 历史
│   ├── router.py                       # AI 兜底 on_message
│   ├── message_adapter.py              # MessageEvent → ChatRequest
│   ├── tool_runtime.py                 # ToolRuntimeFactory
│   │
│   ├── providers/                      # AI Provider（参考 temp/core/agent/providers/）
│   │   ├── base.py                     # ChatProvider / EmbeddingProvider 基类
│   │   ├── register.py                 # 注册装饰器
│   │   ├── manager.py                  # ProviderManager（缓存、切换）
│   │   └── sources/
│   │       ├── openai.py               # OpenAI + Embedding
│   │       ├── anthropic.py            # Claude
│   │       ├── gemini.py               # Gemini
│   │       └── vertex.py               # Vertex AI
│   │
│   ├── tools/                          # 工具系统
│   │   ├── types.py                    # ToolContext, ToolSpec, ToolError
│   │   ├── registry.py                 # ToolRegistry, @tool 装饰器
│   │   ├── executor.py                 # execute_tool()
│   │   └── runtime.py                  # ToolRuntime
│   │
│   └── memory/                         # 记忆系统（可选，接口预留）
│       └── base.py                     # MemoryRetriever 接口
│
├── console/                            # Web 控制台
│   ├── server.py                       # FastAPI 挂载 + 路由注册
│   ├── auth.py                         # Token 认证（32 字符随机，存配置）
│   ├── routes/
│   │   ├── membership.py               # 会员 CRUD、续费码生成/查询/作废
│   │   ├── permissions.py              # 权限配置读写 + 热重载
│   │   ├── config.py                   # 系统配置读写
│   │   └── bots.py                     # 在线 Bot 列表、状态查询
│   └── web/                            # Vue 3 前端
│       └── dist/                       # 发布前构建好，静态文件挂载
│
├── config/                             # 配置系统（ConfigProxy 模式）
│   ├── __init__.py
│   ├── proxy.py                        # ConfigProxy（默认值+深度合并+验证）
│   ├── manager.py                      # ConfigManager，统一调度
│   ├── storage.py                      # JSON 文件读写
│   └── system_defaults.py             # 全局默认配置（会员、控制台等）
├── bootstrap.py                        # 启动初始化（权限缓存、DB init、配置加载）
├── utils/
│   ├── __init__.py
│   ├── compat.py                       # 适配器兼容（is_onebot_v11, build_message_segment）
│   └── http.py                         # 共享 HTTP 客户端
│
└── plugins/                            # 内置子插件（load_plugins 加载）
    ├── entertain/                      # 娱乐：运势、抽卡、点歌、打卡
    ├── group_admin/                    # 群管：禁言、踢出、头衔
    ├── help/                           # 帮助系统
    ├── napcat/                         # napcat 集成
    ├── cultured/                       # 图库
    └── useful/                         # 工具
```

**说明**：
- 子插件放在框架包里，`load_plugins()` 加载。
- 文档中提到的运行时配置路径（如 `data/config/permissions.json`）指运行时配置目录，不是包内 `config/` Python 模块。
- Provider source 导入失败时只禁用对应 Provider，不影响主插件启动。
- `temp/` 只用于查行为和迁移思路，不作为运行时包路径。
- 记忆系统第一版只留 `base.py` 接口，具体实现后置。

---

## 四、核心模块设计

### 4.1 Plugin 类

```python
# plugins/group_admin/__init__.py
from nonebot_plugin_fxbot.plugin.builder import Plugin, PermLevel
from nonebot_plugin_fxbot.chat.tools import tool, ToolContext, ToolRuntime
from . import service

P = Plugin("group_admin", display_name="群管", level=PermLevel.ADMIN)

kick_cmd = P.on_command("kick", name="kick_member", display_name="踢人", priority=5, block=True)

@tool(name="mute_member", description="禁言群成员", parameters={...})
async def mute_member(ctx: ToolContext, rt: ToolRuntime, user_id: int, duration: int):
    bot = rt.require_bot()
    return await service.mute_member(bot, ...)
```

Plugin 通过 `__getattr__` 代理 `on_command` / `on_regex` / `on_message` 等工厂，自动注入权限检查和显示名。

### 4.2 权限系统

```python
# permission/policy.py
PolicyChain([
    EnabledPolicy(),     # enabled=false → DENY
    BlacklistPolicy(),   # 在黑名单 → DENY
    WhitelistPolicy(),   # 不在白名单 → DENY
    ScenePolicy(),       # 场景不匹配 → DENY
    LevelPolicy(),       # 等级不足 → DENY
])
```

纯逻辑，可独立测试。配置存运行时目录（如 `<data_dir>/config/permissions.json`），插件注册时写默认值，控制台修改后热重载。

### 4.3 会员系统（三张表 + 审计）

```
MembershipGroup         # 会员群记录
  - id, group_id, status, expires_at, managed_by_bot, remark, timestamps

RenewCode               # 续费码
  - id, code, duration_value, duration_unit, max_use, used_count, expires_at, status

RenewRecord              # 审计记录
  - id, code, group_id, operator_user_id, used_at, before/after_expires_at
```

**gate.py（event_preprocessor）**：必须最早导入，规则：
- 私聊 → 放行
- SUPERUSER / bot_admin → 放行
- 续费命令白名单（正则 fullmatch）→ 放行
- 群未注册 / 状态非 active / 已过期 → 拦截
- 异常 → fail-closed，静默拦截

### 4.4 控制台

```
console/
├── server.py           # 挂载 FastAPI，注册路由
├── auth.py             # 32 字符 token，存配置，Header Bearer 认证
├── routes/
│   ├── membership.py   # 会员 CRUD、续费码生成/查询/作废
│   ├── permissions.py  # 权限配置读写 + 热重载
│   ├── config.py       # 系统配置读写
│   └── bots.py         # 在线 Bot 列表、状态
└── web/
    └── dist/           # 发布前构建，Python 只挂载静态文件
```

**安全要点**：token 32 字符以上随机，不暴露短 token，运行时不自动 npm install。

### 4.5 AI 对话

第一版保持最小：
- ChatService（对话编排：加载历史 → 调 Provider → 工具循环 → 保存历史）
- Provider 层参考 `temp/core/agent/providers/` 的 OpenAI / Anthropic / Gemini / Vertex 和 ProviderManager
- ChatService 第一版只接入 chat 能力，embedding / tts / stt 可保留代码但不进入主流程
- 基础历史记录

兜底路由规则：
- 私聊 → 默认接管
- 群聊 → 只有 @bot 才接管
- 命令前缀 `#`、`/`、`.` → 跳过

### 4.6 工具系统

保留原项目干净的设计：
- `@tool` 装饰器注册到 ToolRegistry
- `ToolContext` + `ToolRuntime` 注入平台能力
- 工具不直接依赖 NoneBot
- **高风险工具**（禁言/踢人/退群/批量通知）需补权限或确认机制

---

## 五、启动链路

```
nonebot 加载 nonebot_plugin_fxbot
  1. 初始化配置目录
  2. 初始化数据库（SQLite / SQLModel）
  3. 导入 membership.gate     → 注册 event_preprocessor
  4. 导入 membership.commands  → 注册续费/查到期/控制台登录命令
  5. 导入 chat.router          → 注册 AI 兜底 on_message matcher
  6. 初始化权限默认配置 + 缓存
  7. 挂载控制台（失败不影响门禁和权限）
  8. 加载内置子插件（load_plugins "plugins/"）
```

**重点**：
- `membership.gate` 必须早于业务插件
- `membership.commands` 和 `chat.router` 必须在加载子插件前导入，否则 matcher 不注册
- 权限缓存不依赖控制台
- 控制台失败不影响核心功能
- DB 失败时会员门禁 fail-closed

---

## 六、消息流

```
用户消息
  │
  ├─ [event_preprocessor] 群消息缓存（可选）
  │
  ├─ [event_preprocessor] membership.gate 门禁判定
  │   ├─ 私聊 / SUPERUSER / bot_admin → 放行
  │   ├─ 续费命令白名单 → 放行
  │   └─ 非会员群 → 拦截
  │
  ├─ Plugin 命令匹配 (priority=1~50)
  │   ├─ 匹配 → 处理、回复
  │   └─ 不匹配 → fallthrough
  │
  └─ AI Router (priority=99, block=True)
      ├─ 过滤：命令前缀 `#`、`/`、`.` → 跳过
      │         群聊非 @bot → 跳过
      ├─ MessageEvent → ChatRequest
      ├─ ChatService.process(request, runtime)
      └─ ChatResponse → 发送消息
```

---

## 七、依赖

**主依赖**：

```
nonebot2
nonebot-adapter-onebot
pydantic
sqlmodel
sqlalchemy
aiosqlite
httpx
fastapi
```

**可选 extras**：

```
fxbot[ai]         -> openai / anthropic / google-genai
fxbot[memory]     -> faiss / pandas / networkx
fxbot[render]     -> pillow / playwright
```

> Provider source 导入失败时只禁用对应 Provider，不影响主插件启动。

---

## 八、实施顺序

| # | 模块 | 说明 |
|---|------|------|
| 1 | 骨架 | package skeleton、路径管理、基础配置 |
| 2 | db/ | 参考 `temp/db/base_models.py` 实现 `db/base.py` |
| 3 | chat/providers/ | 参考 `temp/core/agent/providers/`，改导入路径和必要命名 |
| 4 | permission/ | 重写：类型 → 策略链 → 存储 → 检查器 |
| 5 | plugin/builder.py | 重写：Plugin 类 |
| 6 | membership/ | 重写：models → service → guard → gate |
| 7 | membership 命令 | 续费、查到期、控制台登录 |
| 8 | console/ | auth → server → routes |
| 9 | chat/ | types → service → router + 工具系统 |
| 10 | 迁移子插件 | 逐一适配新 API |

---

## 九、风险点

| 风险 | 应对 |
|------|------|
| 范围膨胀 | 第一版不做记忆/RAG/MCP，明确边界 |
| 门禁误拦截 | 白名单穿透、SUPERUSER 穿透、私聊不拦截 |
| fail-closed 可用性 | 日志清晰，提供管理员诊断命令 |
| 控制台安全 | 32 字符 token，不暴露短 token |
| 权限 key 稳定性 | 命令 name 发布后不随便改 |
| 前端构建 | 发布前构建 dist，运行时只挂载静态文件 |
| Provider SDK 缺失导致启动失败 | source 导入 try/except，失败只 warn 不 crash |
| chat.router / membership.commands 不注册 | 启动链路中显式 import，确保早于 load_plugins |

---

## 十、核心原则

1. **核心模式重写，重型底座先按 temp 适配** — permission、plugin、membership、console 自己重写；db 和 providers 第一版参考 `temp/` 对应实现，先跑通后瘦身
2. **扁平结构** — 不搞 core/ 和 adapters/ 分层，一目了然；工具函数收进 `utils/`
3. **ConfigProxy 统一管理** — 每个模块有自己的 JSON 配置，支持默认值、深度合并、热重载
4. **子插件放框架包内** — load_plugins 加载，和原项目一致
5. **第一版闭环优先** — 插件注册 + 权限 + 会员门禁 + 控制台 + 基础 AI 对话
