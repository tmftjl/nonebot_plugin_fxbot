"""A_Memorix 配置模型。

原样复制自 MaiBot `src/config/official_configs.py`（commit 062d45cf8bd4272ac57d7ec03fcf405e5a458211），
仅将宿主 `ConfigBase` 替换为最小 pydantic 基类，字段名、默认值与校验保持上游一致。
"""

from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConfigBase(BaseModel):
    """最小配置基类，保持上游 pydantic 行为。"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    __ui_parent__: ClassVar[str] = ""
    __ui_label__: ClassVar[str] = ""
    __ui_advanced__: ClassVar[bool] = False
    __ui_order__: ClassVar[int] = 0
    __ui_use_subtabs__: ClassVar[bool] = False
    __ui_sub_label__: ClassVar[str] = ""
    __ui_root_sub_label__: ClassVar[str] = ""


RULE_TYPE_OPTION_DESCRIPTIONS = {
    "group": "群聊聊天流，item_id 填群号或群聊 ID",
    "private": "私聊聊天流，item_id 填用户 ID",
}


class TargetItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "label": {"zh_CN": "平台", "en_US": "Platform", "ja_JP": "プラットフォーム"},
            "x-widget": "input",
        },
    )
    item_id: str = Field(
        default="",
        json_schema_extra={
            "label": {"zh_CN": "聊天流 ID", "en_US": "Chat stream ID", "ja_JP": "チャットストリーム ID"},
            "x-widget": "input",
        },
    )
    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "label": {"zh_CN": "聊天类型", "en_US": "Chat type", "ja_JP": "チャット種別"},
            "x-widget": "select",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )


class ChatStreamGroup(ConfigBase):
    targets: list[TargetItem] = Field(default_factory=lambda: [])
    """这个组里的聊天流会共享对应的学习内容。"""


class AMemorixIntegrationConfig(ConfigBase):
    """记忆在聊天中的使用"""

    __ui_parent__ = "a_memorix"

    enable_memory_query_tool: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆检索",
                "en_US": "Enable memory search",
                "ja_JP": "記憶検索を有効化",
            },
            "x-widget": "switch",
        },
    )
    """是否允许麦麦在聊天时查询长期记忆"""

    memory_query_default_limit: int = Field(
        default=5,
        ge=1,
        le=20,
        json_schema_extra={
            "label": {
                "zh_CN": "回忆记忆条数",
                "en_US": "Default memory result count",
                "ja_JP": "記憶検索件数",
            },
            "x-widget": "input",
        },
    )
    """每次默认从长期记忆中取回多少条结果"""

    enable_person_profile_query_tool: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用人物画像查询",
                "en_US": "Enable profile search",
                "ja_JP": "人物プロファイル検索を有効化",
            },
            "x-widget": "switch",
        },
    )
    """是否允许麦麦查询人物画像记忆"""

    enable_person_profile_injection: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动注入人物画像",
                "en_US": "Inject person profiles",
                "ja_JP": "人物プロファイルを自動注入",
            },
            "x-widget": "switch",
        },
    )
    """是否在 Maisaka Planner 调用前自动注入当前对象相关的人物画像"""

    person_profile_injection_max_profiles: int = Field(
        default=3,
        ge=1,
        le=5,
        json_schema_extra={
            "label": {
                "zh_CN": "注入画像上限",
                "en_US": "Injected profile limit",
                "ja_JP": "注入プロファイル上限",
            },
            "x-widget": "input",
        },
    )
    """每轮自动注入的人物画像数量上限"""

    heuristic_memory_recall_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启发式拉起记忆",
                "en_US": "Heuristic memory recall",
                "ja_JP": "ヒューリスティック記憶呼び出し",
            },
            "x-widget": "switch",
        },
    )
    """是否根据当前聊天印象自然拉起长期记忆"""

    heuristic_memory_cross_chat_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "允许跨聊天流拉起",
                "en_US": "Allow cross-chat recall",
                "ja_JP": "チャット横断呼び出しを許可",
            },
            "x-widget": "switch",
        },
    )
    """是否允许启发式记忆从其他聊天流召回候选"""

    heuristic_memory_recall_window_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "label": {
                "zh_CN": "印象窗口消息数",
                "en_US": "Impression window size",
                "ja_JP": "印象ウィンドウ件数",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """生成当前聊天印象时使用的最近消息数量"""

    heuristic_memory_recall_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起记忆条数",
                "en_US": "Heuristic memory limit",
                "ja_JP": "自然呼び出し記憶数",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """每轮自然拉起的长期记忆数量上限"""

    heuristic_memory_recall_max_chars: int = Field(
        default=900,
        ge=100,
        le=4000,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起文本上限",
                "en_US": "Heuristic memory text limit",
                "ja_JP": "自然呼び出し文字数上限",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """自然拉起记忆注入文本的最大字符数"""

    heuristic_memory_recall_min_interval_seconds: int = Field(
        default=180,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起冷却秒数",
                "en_US": "Heuristic recall cooldown",
                "ja_JP": "自然呼び出しクールダウン",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """同一聊天流两次自然拉起的最小间隔秒数"""

    heuristic_memory_recall_min_new_messages: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起新增消息阈值",
                "en_US": "Heuristic recall message threshold",
                "ja_JP": "自然呼び出し新規メッセージ閾値",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """两次自然拉起之间至少需要新增的当前聊天流消息数"""

    heuristic_memory_recall_cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起缓存秒数",
                "en_US": "Heuristic recall cache TTL",
                "ja_JP": "自然呼び出しキャッシュ秒数",
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """同一聊天流自然拉起结果的运行时缓存时间"""

    heuristic_memory_group_to_private_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "私聊拉起群聊记忆",
                "en_US": "Group memories in private chats",
                "ja_JP": "個人チャットでグループ記憶を使う",
            },
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """私聊中是否允许自然拉起群聊记忆"""

    heuristic_memory_private_to_group_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "群聊拉起私聊记忆",
                "en_US": "Private memories in group chats",
                "ja_JP": "グループチャットで個人記憶を使う",
            },
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """群聊中是否允许自然拉起私聊记忆"""

    person_fact_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "写回人物事实",
                "en_US": "Write back person facts",
                "ja_JP": "人物事実を書き戻す",
            },
            "x-widget": "switch",
        },
    )
    """是否在发送回复后自动提取并写回人物事实到长期记忆"""

    chat_summary_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "写回聊天摘要",
                "en_US": "Write back chat summaries",
                "ja_JP": "チャット要約を書き戻す",
            },
            "x-widget": "switch",
        },
    )
    """是否在 Maisaka 聊天过程中按消息窗口自动写回聊天摘要到长期记忆"""

    chat_summary_writeback_message_threshold: int = Field(
        default=36,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """自动写回聊天摘要的消息窗口阈值"""

    chat_summary_writeback_context_length: int = Field(
        default=36,
        ge=1,
        le=500,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """自动写回聊天摘要时，从聊天流中回看的消息条数"""

    fuzzy_modify_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否启用自然语言记忆修正的后台接口"""

    fuzzy_modify_auto_execute_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否允许高置信记忆修正跳过人工确认自动执行"""

    fuzzy_modify_confirm_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "x-widget": "slider",
            "step": 0.01,
            "advanced": True,
        },
    )
    """记忆修正建议进入自动确认判定时使用的置信度阈值"""

    fuzzy_modify_candidate_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """每次记忆修正交给 LLM 的候选记忆上限"""

    fuzzy_modify_max_targets: int = Field(
        default=5,
        ge=1,
        le=20,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """单个记忆修正计划允许标记失效的旧记忆上限"""

    fuzzy_modify_allow_global_scope: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """未指定聊天流时，是否允许在全局记忆范围内做记忆修正候选检索"""

    feedback_correction_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否启用反馈驱动的延迟记忆纠错任务"""

    feedback_correction_window_hours: float = Field(
        default=12.0,
        ge=0.1,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """反馈窗口时长（小时），以 query_memory 执行时间为起点"""

    feedback_correction_check_interval_minutes: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """反馈纠错定时任务轮询间隔（分钟）"""

    feedback_correction_batch_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """反馈纠错每轮最大处理任务数"""

    feedback_correction_auto_apply_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "x-widget": "slider",
            "step": 0.01,
            "advanced": True,
        },
    )
    """自动应用纠错动作的最低置信度阈值"""

    feedback_correction_max_feedback_messages: int = Field(
        default=30,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """每个纠错任务最多使用的窗口内用户反馈消息数"""

    feedback_correction_prefilter_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否启用纠错前置预筛（用于减少不必要的模型调用）"""

    feedback_correction_paragraph_mark_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否为受影响 paragraph 写入已纠正旧事实标记"""

    feedback_correction_paragraph_hard_filter_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否在用户侧查询中硬过滤带有 stale 标记的 paragraph"""

    feedback_correction_profile_refresh_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否在反馈纠错后将受影响人物画像加入刷新队列"""

    feedback_correction_profile_force_refresh_on_read: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """人物画像处于脏队列时，读取是否强制刷新而不直接复用旧快照"""

    feedback_correction_episode_rebuild_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """是否在反馈纠错后将受影响 source 加入 episode 重建队列"""

    feedback_correction_episode_query_block_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "advanced": True,
        },
    )
    """episode source 处于重建队列时，是否对用户侧查询做屏蔽"""

    feedback_correction_reconcile_interval_minutes: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """反馈纠错二阶段一致性后台协调任务轮询间隔（分钟）"""

    feedback_correction_reconcile_batch_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "advanced": True,
        },
    )
    """反馈纠错二阶段一致性每轮处理 profile/episode 队列的批大小"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置值"""
        if not 0 <= self.fuzzy_modify_confirm_threshold <= 1:
            raise ValueError(
                "fuzzy_modify_confirm_threshold 必须在 [0, 1] 之间，"
                f"当前值: {self.fuzzy_modify_confirm_threshold}"
            )
        if self.fuzzy_modify_candidate_limit < 1:
            raise ValueError(
                "fuzzy_modify_candidate_limit 必须至少为1，"
                f"当前值: {self.fuzzy_modify_candidate_limit}"
            )
        if self.fuzzy_modify_max_targets < 1:
            raise ValueError(
                f"fuzzy_modify_max_targets 必须至少为1，当前值: {self.fuzzy_modify_max_targets}"
            )
        if self.feedback_correction_window_hours <= 0:
            raise ValueError(
                f"feedback_correction_window_hours 必须大于0，当前值: {self.feedback_correction_window_hours}"
            )
        if self.feedback_correction_check_interval_minutes < 1:
            raise ValueError(
                "feedback_correction_check_interval_minutes 必须至少为1，"
                f"当前值: {self.feedback_correction_check_interval_minutes}"
            )
        if self.feedback_correction_batch_size < 1:
            raise ValueError(
                f"feedback_correction_batch_size 必须至少为1，当前值: {self.feedback_correction_batch_size}"
            )
        if not 0 <= self.feedback_correction_auto_apply_threshold <= 1:
            raise ValueError(
                "feedback_correction_auto_apply_threshold 必须在 [0, 1] 之间，"
                f"当前值: {self.feedback_correction_auto_apply_threshold}"
            )
        if self.feedback_correction_max_feedback_messages < 1:
            raise ValueError(
                "feedback_correction_max_feedback_messages 必须至少为1，"
                f"当前值: {self.feedback_correction_max_feedback_messages}"
            )
        if self.feedback_correction_reconcile_interval_minutes < 1:
            raise ValueError(
                "feedback_correction_reconcile_interval_minutes 必须至少为1，"
                f"当前值: {self.feedback_correction_reconcile_interval_minutes}"
            )
        if self.feedback_correction_reconcile_batch_size < 1:
            raise ValueError(
                "feedback_correction_reconcile_batch_size 必须至少为1，"
                f"当前值: {self.feedback_correction_reconcile_batch_size}"
            )
        return super().model_post_init(context)


class AMemorixPluginConfig(ConfigBase):
    """记忆系统  A-Memorix"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆",
                "en_US": "Enable memory",
                "ja_JP": "記憶を有効化",
            },
        },
    )
    """是否启用长期记忆系统"""


class AMemorixStorageConfig(ConfigBase):
    """A_Memorix 存储位置"""

    data_dir: str = Field(
        default="data/a-memorix",
        json_schema_extra={
            "label": {
                "zh_CN": "数据目录",
                "en_US": "Data directory",
                "ja_JP": "データディレクトリ",
            },
        },
    )
    """数据目录"""


class AMemorixImageMemoryConfig(ConfigBase):
    """A_Memorix 图片资产、向量任务和相似召回配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用图片记忆",
                "en_US": "Enable image memory",
                "ja_JP": "画像記憶を有効化",
            }
        },
    )
    task_name: str = Field(
        default="image_embedding",
        json_schema_extra={
            "label": {
                "zh_CN": "图片嵌入任务",
                "en_US": "Image embedding task",
                "ja_JP": "画像埋め込みタスク",
            }
        },
    )
    preprocess_version: str = Field(
        default="identity_v1",
        json_schema_extra={"label": {"zh_CN": "预处理版本", "en_US": "Preprocess version", "ja_JP": "前処理バージョン"}},
    )
    probe_retry_seconds: float = Field(
        default=60.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "模型探测重试间隔",
                "en_US": "Model probe retry interval",
                "ja_JP": "モデル確認の再試行間隔",
            }
        },
    )
    max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        json_schema_extra={"label": {"zh_CN": "单图大小上限", "en_US": "Image size limit", "ja_JP": "画像サイズ上限"}},
    )
    max_pixels: int = Field(
        default=40_000_000,
        ge=1,
        json_schema_extra={"label": {"zh_CN": "单图像素上限", "en_US": "Image pixel limit", "ja_JP": "画像ピクセル上限"}},
    )
    candidate_limit: int = Field(
        default=8,
        ge=1,
        le=100,
        json_schema_extra={"label": {"zh_CN": "相似候选数量", "en_US": "Similar candidate count", "ja_JP": "類似候補数"}},
    )
    similarity_threshold: float = Field(
        default=0.72,
        ge=-1.0,
        le=1.0,
        json_schema_extra={"label": {"zh_CN": "相似度阈值", "en_US": "Similarity threshold", "ja_JP": "類似度しきい値"}},
    )
    job_poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.1,
        json_schema_extra={"label": {"zh_CN": "任务轮询间隔", "en_US": "Job polling interval", "ja_JP": "ジョブ確認間隔"}},
    )
    job_batch_size: int = Field(
        default=4,
        ge=1,
        le=100,
        json_schema_extra={"label": {"zh_CN": "任务处理批量", "en_US": "Job batch size", "ja_JP": "ジョブ処理バッチ数"}},
    )
    job_enqueue_batch_size: int = Field(
        default=200,
        ge=1,
        json_schema_extra={"label": {"zh_CN": "任务入队批量", "en_US": "Job enqueue batch", "ja_JP": "ジョブ登録バッチ数"}},
    )
    job_lease_seconds: float = Field(
        default=120.0,
        ge=1.0,
        json_schema_extra={"label": {"zh_CN": "任务租约秒数", "en_US": "Job lease seconds", "ja_JP": "ジョブリース秒数"}},
    )
    job_max_retries: int = Field(
        default=5,
        ge=0,
        json_schema_extra={"label": {"zh_CN": "任务最大重试", "en_US": "Maximum job retries", "ja_JP": "ジョブ最大再試行回数"}},
    )
    min_train_threshold: int = Field(
        default=40,
        ge=1,
        json_schema_extra={"label": {"zh_CN": "索引训练最小样本", "en_US": "Minimum index training samples", "ja_JP": "索引学習の最小サンプル数"}},
    )


class AMemorixEmbeddingFallbackConfig(ConfigBase):
    """A_Memorix Embedding 回退"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回退",
                "en_US": "Enable fallback",
                "ja_JP": "フォールバックを有効化",
            },
        },
    )
    """是否启用回退机制"""

    probe_interval_seconds: int = Field(
        default=180,
        ge=10,
        json_schema_extra={
            "label": {
                "zh_CN": "探测间隔",
                "en_US": "Probe interval",
                "ja_JP": "プローブ間隔",
            },
        },
    )
    """探测间隔秒数"""

    allow_metadata_only_write: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "允许仅写元数据",
                "en_US": "Allow metadata-only writes",
                "ja_JP": "メタデータのみの書き込みを許可",
            },
        },
    )
    """是否允许仅写入元数据"""


class AMemorixParagraphVectorBackfillConfig(ConfigBase):
    """A_Memorix 段落向量回填"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回填",
                "en_US": "Enable backfill",
                "ja_JP": "バックフィルを有効化",
            },
        },
    )
    """是否启用回填任务"""

    interval_seconds: int = Field(
        default=60,
        ge=5,
        json_schema_extra={
            "label": {
                "zh_CN": "回填间隔",
                "en_US": "Backfill interval",
                "ja_JP": "バックフィル間隔",
            },
        },
    )
    """回填轮询间隔"""

    batch_size: int = Field(
        default=64,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "批量大小",
                "en_US": "Batch size",
                "ja_JP": "バッチサイズ",
            },
        },
    )
    """单批回填数量"""

    max_retry: int = Field(
        default=5,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "最大重试",
                "en_US": "Max retries",
                "ja_JP": "最大リトライ回数",
            },
        },
    )
    """最大重试次数"""


class AMemorixEmbeddingConfig(ConfigBase):
    """记忆向量化配置"""

    model_name: str = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "向量化模型",
                "en_US": "Embedding model",
                "ja_JP": "Embedding モデル",
            },
        },
    )
    """用于把记忆内容转换成向量的模型，auto 表示自动选择"""

    dimension: int = Field(
        default=1024,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "向量维度",
                "en_US": "Vector dimension",
                "ja_JP": "ベクトル次元",
            },
        },
    )
    """记忆向量的维度，需要与向量化模型保持一致"""

    dimension_request_mode: Literal["explicit", "always", "never"] = Field(
        default="explicit",
        json_schema_extra={
            "label": {
                "zh_CN": "维度请求模式",
                "en_US": "Dimension request mode",
                "ja_JP": "次元リクエストモード",
            },
        },
    )
    """是否在 embedding 请求中携带维度参数：explicit 仅显式指定时携带，always 总是携带，never 不携带"""

    batch_size: int = Field(
        default=32,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单批数量",
                "en_US": "Batch size",
                "ja_JP": "バッチサイズ",
            },
        },
    )
    """每次向量化请求处理的记忆条数"""

    max_concurrent: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大并发数",
                "en_US": "Max concurrency",
                "ja_JP": "最大同時実行数",
            },
        },
    )
    """同时进行的向量化请求数量"""

    enable_cache: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用向量缓存",
                "en_US": "Enable cache",
                "ja_JP": "キャッシュを有効化",
            },
        },
    )
    """是否缓存向量化结果"""

    runtime_train_threshold: int = Field(
        default=256,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "运行期向量训练阈值",
                "en_US": "Runtime vector training threshold",
                "ja_JP": "実行時ベクトル学習しきい値",
            },
        },
    )
    """未训练向量池在运行期间触发 SQ8 后台训练所需的最少向量数"""

    quantization_type: Literal["int8"] = Field(
        default="int8",
        json_schema_extra={
            "label": {
                "zh_CN": "量化方式",
                "en_US": "Quantization type",
                "ja_JP": "量子化方式",
            },
        },
    )
    """向量压缩方式，当前仅支持 int8(SQ8)"""

    fallback: AMemorixEmbeddingFallbackConfig = Field(
        default_factory=AMemorixEmbeddingFallbackConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Embedding 回退",
                "en_US": "Embedding fallback",
                "ja_JP": "Embedding フォールバック",
            },
        },
    )
    """Embedding 回退配置"""

    paragraph_vector_backfill: AMemorixParagraphVectorBackfillConfig = Field(
        default_factory=AMemorixParagraphVectorBackfillConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "段落向量回填",
                "en_US": "Paragraph vector backfill",
                "ja_JP": "段落ベクトルバックフィル",
            },
        },
    )
    """段落向量回填配置"""


class AMemorixSparseRetrievalConfig(ConfigBase):
    """A_Memorix 稀疏检索配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用稀疏检索",
                "en_US": "Enable sparse retrieval",
                "ja_JP": "疎検索を有効化",
            },
        },
    )
    """是否启用稀疏检索"""

    backend: Literal["fts5"] = Field(
        default="fts5",
        json_schema_extra={
            "label": {
                "zh_CN": "检索后端",
                "en_US": "Retrieval backend",
                "ja_JP": "検索バックエンド",
            },
        },
    )
    """稀疏检索后端"""

    mode: Literal["auto", "fallback_only", "hybrid"] = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "检索模式",
                "en_US": "Retrieval mode",
                "ja_JP": "検索モード",
            },
        },
    )
    """稀疏检索模式"""

    tokenizer_mode: Literal["jieba", "mixed", "char_2gram"] = Field(
        default="jieba",
        json_schema_extra={
            "label": {
                "zh_CN": "分词模式",
                "en_US": "Tokenizer mode",
                "ja_JP": "トークナイザーモード",
            },
        },
    )
    """分词模式"""

    candidate_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落候选数",
                "en_US": "Paragraph candidates",
                "ja_JP": "段落候補数",
            },
        },
    )
    """段落候选数"""

    relation_candidate_k: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系候选数",
                "en_US": "Relation candidates",
                "ja_JP": "関係候補数",
            },
        },
    )
    """关系候选数"""


class AMemorixSmartFallbackConfig(ConfigBase):
    """A_Memorix 智能兜底检索配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用智能兜底",
                "en_US": "Enable smart fallback",
                "ja_JP": "スマートフォールバックを有効化",
            },
        },
    )
    """是否启用智能兜底检索"""


class AMemorixRetrievalSearchConfig(ConfigBase):
    """A_Memorix 搜索后处理配置"""

    smart_fallback: AMemorixSmartFallbackConfig = Field(
        default_factory=AMemorixSmartFallbackConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "智能兜底",
                "en_US": "Smart fallback",
                "ja_JP": "スマートフォールバック",
            },
        },
    )
    """智能兜底检索配置"""


class AMemorixFusionRetrievalConfig(ConfigBase):
    """A_Memorix 检索融合配置"""

    method: Literal["weighted_rrf", "alpha_legacy"] = Field(
        default="weighted_rrf",
        json_schema_extra={
            "label": {
                "zh_CN": "融合方法",
                "en_US": "Fusion method",
                "ja_JP": "融合方式",
            },
        },
    )
    """检索融合方法"""

    rrf_k: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "RRF K",
                "en_US": "RRF K",
                "ja_JP": "RRF K",
            },
        },
    )
    """RRF 融合参数"""

    vector_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "向量权重",
                "en_US": "Vector weight",
                "ja_JP": "ベクトル重み",
            },
        },
    )
    """向量检索权重"""

    bm25_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "BM25 权重",
                "en_US": "BM25 weight",
                "ja_JP": "BM25 重み",
            },
        },
    )
    """BM25 稀疏检索权重"""


class AMemorixRelationVectorizationConfig(ConfigBase):
    """A_Memorix 关系向量化配置"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用关系向量",
                "en_US": "Enable relation vectors",
                "ja_JP": "関係ベクトルを有効化",
            },
        },
    )
    """是否启用关系向量化"""

    backfill_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用关系向量回填",
                "en_US": "Enable relation vector backfill",
                "ja_JP": "関係ベクトルバックフィルを有効化",
            },
        },
    )
    """是否启用关系向量回填"""

    write_on_import: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "导入时写入关系向量",
                "en_US": "Write relation vectors on import",
                "ja_JP": "インポート時に関係ベクトルを書き込む",
            },
        },
    )
    """导入时是否写入关系向量"""


class AMemorixRelationIntentVectorPoolConfig(ConfigBase):
    """A_Memorix 关系意图下的双向量池配置"""

    graph_top_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图图谱候选数",
                "en_US": "Relation-intent graph candidates",
                "ja_JP": "関係意図グラフ候補数",
            },
        },
    )
    """关系意图命中时的图谱池候选数"""

    semantic_weight: float = Field(
        default=0.45,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图语义权重",
                "en_US": "Relation-intent semantic weight",
                "ja_JP": "関係意図セマンティック重み",
            },
        },
    )
    """关系意图命中时的段落语义权重"""

    sparse_weight: float = Field(
        default=0.15,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图稀疏权重",
                "en_US": "Relation-intent sparse weight",
                "ja_JP": "関係意図疎検索重み",
            },
        },
    )
    """关系意图命中时的稀疏检索权重"""

    graph_weight: float = Field(
        default=0.40,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图图谱权重",
                "en_US": "Relation-intent graph weight",
                "ja_JP": "関係意図グラフ重み",
            },
        },
    )
    """关系意图命中时的图谱证据权重"""

    return_relation_items: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "返回独立关系项",
                "en_US": "Return relation items",
                "ja_JP": "独立した関係項目を返す",
            },
        },
    )
    """关系意图命中时是否返回独立关系结果"""


class AMemorixVectorPoolsConfig(ConfigBase):
    """A_Memorix 双向量池检索配置"""

    mode: Literal["single", "dual"] = Field(
        default="dual",
        json_schema_extra={
            "label": {
                "zh_CN": "向量池模式",
                "en_US": "Vector pool mode",
                "ja_JP": "ベクトルプールモード",
            },
        },
    )
    """向量池模式"""

    paragraph_top_k: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落池候选数",
                "en_US": "Paragraph pool candidates",
                "ja_JP": "段落プール候補数",
            },
        },
    )
    """段落向量池候选数"""

    graph_top_k: int = Field(
        default=40,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱池候选数",
                "en_US": "Graph pool candidates",
                "ja_JP": "グラフプール候補数",
            },
        },
    )
    """图谱向量池候选数"""

    graph_expand_paragraph_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱展开段落上限",
                "en_US": "Graph expanded paragraph limit",
                "ja_JP": "グラフ展開段落上限",
            },
        },
    )
    """图谱证据展开段落上限"""

    relation_expand_per_hit: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "每个关系展开段落数",
                "en_US": "Paragraphs per relation hit",
                "ja_JP": "関係ヒットごとの段落数",
            },
        },
    )
    """每个关系命中最多展开的段落数"""

    entity_expand_per_hit: int = Field(
        default=8,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "每个实体展开段落数",
                "en_US": "Paragraphs per entity hit",
                "ja_JP": "エンティティヒットごとの段落数",
            },
        },
    )
    """每个实体命中最多展开的段落数"""

    relation_evidence_weight: float = Field(
        default=1.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系证据权重",
                "en_US": "Relation evidence weight",
                "ja_JP": "関係証拠重み",
            },
        },
    )
    """关系证据分权重"""

    entity_evidence_weight: float = Field(
        default=0.55,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "实体证据权重",
                "en_US": "Entity evidence weight",
                "ja_JP": "エンティティ証拠重み",
            },
        },
    )
    """实体证据分权重"""

    semantic_weight: float = Field(
        default=0.65,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "段落语义权重",
                "en_US": "Paragraph semantic weight",
                "ja_JP": "段落セマンティック重み",
            },
        },
    )
    """段落语义分权重"""

    sparse_weight: float = Field(
        default=0.20,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "稀疏检索权重",
                "en_US": "Sparse retrieval weight",
                "ja_JP": "疎検索重み",
            },
        },
    )
    """稀疏检索分权重"""

    graph_weight: float = Field(
        default=0.15,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱证据权重",
                "en_US": "Graph evidence weight",
                "ja_JP": "グラフ証拠重み",
            },
        },
    )
    """图谱证据分权重"""

    relation_intent: AMemorixRelationIntentVectorPoolConfig = Field(
        default_factory=AMemorixRelationIntentVectorPoolConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图权重",
                "en_US": "Relation intent weights",
                "ja_JP": "関係意図重み",
            },
        },
    )
    """关系意图命中时的双向量池配置"""


class AMemorixRetrievalConfig(ConfigBase):
    """A_Memorix 检索配置"""

    top_k_paragraphs: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落 Top-K",
                "en_US": "Paragraph Top-K",
                "ja_JP": "段落 Top-K",
            },
        },
    )
    """段落候选数"""

    top_k_relations: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系 Top-K",
                "en_US": "Relation Top-K",
                "ja_JP": "関係 Top-K",
            },
        },
    )
    """关系候选数"""

    top_k_final: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最终 Top-K",
                "en_US": "Final Top-K",
                "ja_JP": "最終 Top-K",
            },
        },
    )
    """最终返回条数"""

    alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系融合权重",
                "en_US": "Relation fusion weight",
                "ja_JP": "関係融合重み",
            },
        },
    )
    """关系融合权重"""

    enable_ppr: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用 PPR",
                "en_US": "Enable PPR",
                "ja_JP": "PPR を有効化",
            },
        },
    )
    """是否启用 PPR"""

    ppr_alpha: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR Alpha",
                "en_US": "PPR alpha",
                "ja_JP": "PPR Alpha",
            },
        },
    )
    """PPR alpha"""

    ppr_timeout_seconds: float = Field(
        default=1.5,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR 超时",
                "en_US": "PPR timeout",
                "ja_JP": "PPR タイムアウト",
            },
        },
    )
    """PPR 超时秒数"""

    ppr_concurrency_limit: int = Field(
        default=4,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR 并发限制",
                "en_US": "PPR concurrency limit",
                "ja_JP": "PPR 同時実行制限",
            },
        },
    )
    """PPR 并发限制"""

    enable_parallel: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "并行检索",
                "en_US": "Parallel retrieval",
                "ja_JP": "並列検索",
            },
        },
    )
    """是否启用并行检索"""

    search: AMemorixRetrievalSearchConfig = Field(
        default_factory=AMemorixRetrievalSearchConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "搜索后处理",
                "en_US": "Search post-processing",
                "ja_JP": "検索後処理",
            },
        },
    )
    """搜索后处理配置"""

    fusion: AMemorixFusionRetrievalConfig = Field(
        default_factory=AMemorixFusionRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "检索融合",
                "en_US": "Retrieval fusion",
                "ja_JP": "検索融合",
            },
        },
    )
    """检索融合配置"""

    relation_vectorization: AMemorixRelationVectorizationConfig = Field(
        default_factory=AMemorixRelationVectorizationConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "关系向量化",
                "en_US": "Relation vectorization",
                "ja_JP": "関係ベクトル化",
            },
        },
    )
    """关系向量化配置"""

    vector_pools: AMemorixVectorPoolsConfig = Field(
        default_factory=AMemorixVectorPoolsConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "向量池检索",
                "en_US": "Vector pool retrieval",
                "ja_JP": "ベクトルプール検索",
            },
        },
    )
    """双向量池检索配置"""

    sparse: AMemorixSparseRetrievalConfig = Field(
        default_factory=AMemorixSparseRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "稀疏检索",
                "en_US": "Sparse retrieval",
                "ja_JP": "疎検索",
            },
        },
    )
    """稀疏检索配置"""


class AMemorixThresholdConfig(ConfigBase):
    """A_Memorix 阈值过滤配置"""

    min_threshold: float = Field(
        default=0.29,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "最小阈值",
                "en_US": "Minimum threshold",
                "ja_JP": "最小しきい値",
            },
        },
    )
    """最小阈值"""

    max_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "最大阈值",
                "en_US": "Maximum threshold",
                "ja_JP": "最大しきい値",
            },
        },
    )
    """最大阈值"""

    percentile: int = Field(
        default=75,
        ge=0,
        le=100,
        json_schema_extra={
            "label": {
                "zh_CN": "动态百分位",
                "en_US": "Dynamic percentile",
                "ja_JP": "動的パーセンタイル",
            },
        },
    )
    """动态阈值百分位"""

    min_results: int = Field(
        default=4,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最小保留数",
                "en_US": "Minimum results",
                "ja_JP": "最小保持件数",
            },
        },
    )
    """最小保留条数"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        if self.min_threshold >= self.max_threshold:
            raise ValueError("min_threshold 必须小于 max_threshold")
        return super().model_post_init(context)

class AMemorixRetrievalSubtypeFilterConfig(ConfigBase):
    """A_Memorix 跨聊天流检索结果分类型过滤配置"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用跨聊天流过滤",
                "en_US": "Enable cross-chat filter",
                "ja_JP": "チャット横断フィルターを有効化",
            },
        },
    )
    """是否启用当前检索结果类型的跨聊天流过滤"""

    mode: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        json_schema_extra={
            "label": {
                "zh_CN": "过滤模式",
                "en_US": "Filter mode",
                "ja_JP": "フィルターモード",
            },
        },
    )
    """过滤模式"""

    chats: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流列表",
                "en_US": "Chat stream list",
                "ja_JP": "チャットストリーム一覧",
            },
        },
    )
    """聊天流列表"""


class AMemorixRetrievalFilterConfig(ConfigBase):
    """A_Memorix 跨聊天流检索结果后置过滤配置"""

    chat_stream: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "普通聊天流记忆",
                "en_US": "Chat stream memory",
                "ja_JP": "通常チャット記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """普通 paragraph/relation 命中的跨聊天流检索后置过滤"""

    chat_summary: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天总结记忆",
                "en_US": "Chat summary memory",
                "ja_JP": "チャット要約記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """聊天总结命中的跨聊天流检索后置过滤"""

    episode: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Episode 记忆",
                "en_US": "Episode memory",
                "ja_JP": "Episode 記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """Episode 命中的跨聊天流检索后置过滤"""


class AMemorixFilterConfig(ConfigBase):
    """聊天过滤配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用聊天过滤",
                "en_US": "Enable chat filter",
                "ja_JP": "チャットフィルターを有効化",
            },
        },
    )
    """是否启用聊天过滤"""

    mode: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        json_schema_extra={
            "label": {
                "zh_CN": "过滤模式",
                "en_US": "Filter mode",
                "ja_JP": "フィルターモード",
            },
        },
    )
    """过滤模式"""

    chats: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流列表",
                "en_US": "Chat stream list",
                "ja_JP": "チャットストリーム一覧",
            },
        },
    )
    """聊天流列表"""

    retrieval: AMemorixRetrievalFilterConfig = Field(
        default_factory=AMemorixRetrievalFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "跨聊天流检索结果过滤",
                "en_US": "Cross-chat retrieval result filter",
                "ja_JP": "チャット横断検索結果フィルター",
            },
            "x-collapsed-by-default": True,
        },
    )
    """仅对跨聊天流检索结果生效的分类型过滤，不影响本聊天流读取自身记忆、写入和后台生成"""


class AMemorixEpisodeConfig(ConfigBase):
    """A_Memorix Episode 配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用 Episode",
                "en_US": "Enable episodes",
                "ja_JP": "Episode を有効化",
            },
        },
    )
    """是否启用 Episode"""

    generation_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动生成 Episode",
                "en_US": "Generate episodes",
                "ja_JP": "Episode を自動生成",
            },
        },
    )
    """是否启用自动生成"""

    source_poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "来源任务轮询间隔",
                "en_US": "Source task polling interval",
                "ja_JP": "ソースタスクのポーリング間隔",
            },
        },
    )
    """来源级 Episode 任务轮询间隔秒数"""

    source_batch_size: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "来源任务批量",
                "en_US": "Source task batch size",
                "ja_JP": "ソースタスクのバッチサイズ",
            },
        },
    )
    """单轮领取的来源任务数"""

    source_max_retry: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "来源任务最大尝试次数",
                "en_US": "Source task max attempts",
                "ja_JP": "ソースタスクの最大試行回数",
            },
        },
    )
    """每个来源版本的最大尝试次数，包含首次尝试"""

    source_lease_seconds: float = Field(
        default=1800.0,
        ge=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "来源任务租约时长",
                "en_US": "Source task lease duration",
                "ja_JP": "ソースタスクのリース時間",
            },
        },
    )
    """来源任务租约时长秒数"""

    source_max_wait_seconds: float = Field(
        default=60.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "来源任务最大防抖等待",
                "en_US": "Source task maximum debounce wait",
                "ja_JP": "ソースタスクの最大デバウンス待機時間",
            },
        },
    )
    """来源持续写入时允许的最大防抖等待秒数"""

    max_paragraphs_per_call: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单次段落上限",
                "en_US": "Paragraphs per call",
                "ja_JP": "1回あたりの段落上限",
            },
        },
    )
    """单次最大段落数"""

    max_chars_per_call: int = Field(
        default=6000,
        ge=100,
        json_schema_extra={
            "label": {
                "zh_CN": "单次字符上限",
                "en_US": "Characters per call",
                "ja_JP": "1回あたりの文字上限",
            },
        },
    )
    """单次最大字符数"""

    source_time_window_hours: float = Field(
        default=24.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "来源时间窗口",
                "en_US": "Source time window",
                "ja_JP": "ソース時間窓",
            },
        },
    )
    """时间窗口小时数"""

    segmentation_model: str = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "分段模型",
                "en_US": "Segmentation model",
                "ja_JP": "分割モデル",
            },
        },
    )
    """分段模型选择"""

    disabled_source_types: List[str] = Field(
        default_factory=lambda: ["person_fact"],
        json_schema_extra={
            "label": {
                "zh_CN": "跳过来源类型",
                "en_US": "Disabled source types",
                "ja_JP": "スキップするソース種別",
            },
            "advanced": True,
        },
    )
    """自动生成 Episode 时跳过的来源类型"""


class AMemorixPersonProfileConfig(ConfigBase):
    """人物画像配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用人物画像",
                "en_US": "Enable person profiles",
                "ja_JP": "人物プロファイルを有効化",
            },
        },
    )
    """是否启用画像"""

    refresh_interval_minutes: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新间隔",
                "en_US": "Refresh interval",
                "ja_JP": "更新間隔",
            },
        },
    )
    """刷新间隔分钟数"""

    active_window_hours: float = Field(
        default=72.0,
        ge=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "活跃窗口",
                "en_US": "Active window",
                "ja_JP": "アクティブ期間",
            },
        },
    )
    """活跃窗口小时数"""

    max_refresh_per_cycle: int = Field(
        default=50,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单轮刷新上限",
                "en_US": "Refreshes per cycle",
                "ja_JP": "1サイクル更新上限",
            },
        },
    )
    """单轮最大刷新数"""

    refresh_debounce_seconds: int = Field(
        default=120,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新静默期",
                "en_US": "Refresh debounce",
                "ja_JP": "更新デバウンス",
            },
            "advanced": True,
        },
    )
    """写入触发画像刷新前等待的静默秒数"""

    refresh_queue_interval_seconds: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新队列间隔",
                "en_US": "Refresh queue interval",
                "ja_JP": "更新キュー間隔",
            },
            "advanced": True,
        },
    )
    """画像刷新队列扫描间隔秒数"""

    refresh_queue_batch_size: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新队列批量",
                "en_US": "Refresh queue batch size",
                "ja_JP": "更新キューバッチサイズ",
            },
            "advanced": True,
        },
    )
    """画像刷新队列单轮处理人数"""

    refresh_retry_backoff_seconds: int = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新重试等待",
                "en_US": "Refresh retry backoff",
                "ja_JP": "更新リトライ待機",
            },
            "advanced": True,
        },
    )
    """画像刷新失败后再次重试前等待的秒数"""

    max_retry: int = Field(
        default=3,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新最大重试",
                "en_US": "Refresh max retries",
                "ja_JP": "更新最大リトライ",
            },
            "advanced": True,
        },
    )
    """画像刷新队列最大重试次数"""

    top_k_evidence: int = Field(
        default=12,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "证据条数",
                "en_US": "Evidence count",
                "ja_JP": "証拠件数",
            },
        },
    )
    """证据条数"""

    evidence_classification_max_tokens: int = Field(
        default=1200,
        ge=128,
        json_schema_extra={
            "label": {
                "zh_CN": "证据分类输出上限",
                "en_US": "Evidence classification max tokens",
                "ja_JP": "証拠分類の最大トークン数",
            },
        },
    )
    """人物画像证据分类最大输出 token 数"""


class AMemorixMemoryEvolutionConfig(ConfigBase):
    """A_Memorix 记忆演化配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆演化",
                "en_US": "Enable memory evolution",
                "ja_JP": "記憶進化を有効化",
            },
        },
    )
    """是否启用记忆演化"""

    half_life_hours: float = Field(
        default=24.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "半衰期",
                "en_US": "Half-life",
                "ja_JP": "半減期",
            },
        },
    )
    """半衰期小时数"""

    prune_threshold: float = Field(
        default=0.1,
        gt=0.0,
        lt=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "裁剪阈值",
                "en_US": "Prune threshold",
                "ja_JP": "剪定しきい値",
            },
        },
    )
    """裁剪阈值"""

    freeze_duration_hours: float = Field(
        default=24.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "冻结时长",
                "en_US": "Freeze duration",
                "ja_JP": "凍結時間",
            },
        },
    )
    """冻结时长小时数"""

    revive_threshold: float = Field(
        default=0.15,
        gt=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "恢复阈值",
                "en_US": "Revival threshold",
                "ja_JP": "復帰しきい値",
            },
        },
    )
    """冻结关系恢复为活跃状态的保留强度阈值"""

    access_reinforcement_alpha: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "访问加强系数",
                "en_US": "Access reinforcement factor",
                "ja_JP": "アクセス強化係数",
            },
        },
    )
    """记忆被最终采用时的饱和加强系数"""

    access_reinforcement_cooldown_minutes: float = Field(
        default=60.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "访问加强冷却时间",
                "en_US": "Access reinforcement cooldown",
                "ja_JP": "アクセス強化クールダウン",
            },
        },
    )
    """同一关系两次访问加强之间的最短分钟数，0表示不限制"""

    explicit_reinforcement_alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "显式加强系数",
                "en_US": "Explicit reinforcement factor",
                "ja_JP": "明示的強化係数",
            },
        },
    )
    """用户显式加强或独立新证据的饱和加强系数"""

    weaken_alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "弱化系数",
                "en_US": "Weakening factor",
                "ja_JP": "弱化係数",
            },
        },
    )
    """显式弱化事件的比例系数"""

    lifecycle_batch_size: int = Field(
        default=1000,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "生命周期批量",
                "en_US": "Lifecycle batch size",
                "ja_JP": "ライフサイクルのバッチサイズ",
            },
        },
    )
    """单轮处理的到期关系数量"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        if self.revive_threshold <= self.prune_threshold:
            raise ValueError("revive_threshold 必须大于 prune_threshold")
        return super().model_post_init(context)


class AMemorixAdvancedConfig(ConfigBase):
    """A_Memorix 高级运行时配置"""

    enable_auto_save: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动保存",
                "en_US": "Auto save",
                "ja_JP": "自動保存",
            },
        },
    )
    """是否启用自动保存"""

    auto_save_interval_minutes: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "自动保存间隔",
                "en_US": "Auto-save interval",
                "ja_JP": "自動保存間隔",
            },
        },
    )
    """自动保存间隔"""

    debug: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "调试模式",
                "en_US": "Debug mode",
                "ja_JP": "デバッグモード",
            },
        },
    )
    """是否启用调试"""


class AMemorixWebImportTimeoutConfig(ConfigBase):
    """A_Memorix 导入中心超时配置"""

    llm_call_seconds: float = Field(
        default=240.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "LLM 单次调用超时",
                "en_US": "LLM call timeout",
                "ja_JP": "LLM 呼び出しタイムアウト",
            },
        },
    )
    """Web 导入中单次 LLM 抽取调用的超时时间，0 表示不额外限制"""


class AMemorixWebImportConfig(ConfigBase):
    """A_Memorix 导入中心配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用导入中心",
                "en_US": "Enable import center",
                "ja_JP": "インポートセンターを有効化",
            },
        },
    )
    """是否启用导入中心"""

    max_queue_size: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大队列",
                "en_US": "Max queue size",
                "ja_JP": "最大キューサイズ",
            },
        },
    )
    """最大队列长度"""

    max_files_per_task: int = Field(
        default=200,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单任务文件上限",
                "en_US": "Files per task",
                "ja_JP": "タスクあたりのファイル上限",
            },
        },
    )
    """单任务最大文件数"""

    max_file_size_mb: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单文件大小上限",
                "en_US": "File size limit",
                "ja_JP": "ファイルサイズ上限",
            },
        },
    )
    """单文件大小上限 MB"""

    max_paste_chars: int = Field(
        default=200000,
        ge=100,
        json_schema_extra={
            "label": {
                "zh_CN": "粘贴字符上限",
                "en_US": "Paste character limit",
                "ja_JP": "貼り付け文字数上限",
            },
        },
    )
    """粘贴字符数上限"""

    default_file_concurrency: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认文件并发",
                "en_US": "Default file concurrency",
                "ja_JP": "既定ファイル並列数",
            },
        },
    )
    """默认文件并发"""

    default_chunk_concurrency: int = Field(
        default=4,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认分块并发",
                "en_US": "Default chunk concurrency",
                "ja_JP": "既定チャンク並列数",
            },
        },
    )
    """默认分块并发"""

    default_narrative_window_size: int = Field(
        default=1600,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "默认叙事抽取窗口",
                "en_US": "Default narrative extraction window",
                "ja_JP": "既定ナラティブ抽出ウィンドウ",
            },
            "advanced": True,
        },
    )
    """默认叙事抽取窗口字符数"""

    default_narrative_overlap: int = Field(
        default=400,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "默认叙事重叠字符",
                "en_US": "Default narrative overlap",
                "ja_JP": "既定ナラティブ重複文字数",
            },
            "advanced": True,
        },
    )
    """默认叙事窗口重叠字符数"""

    default_factual_target_size: int = Field(
        default=1200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "默认事实分块目标",
                "en_US": "Default factual chunk target",
                "ja_JP": "既定ファクトチャンク目標",
            },
            "advanced": True,
        },
    )
    """默认事实分块目标字符数"""

    max_chunk_chars: int = Field(
        default=3200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "分块字符上限",
                "en_US": "Chunk character limit",
                "ja_JP": "チャンク文字数上限",
            },
            "advanced": True,
        },
    )
    """单个抽取分块的字符上限"""

    timeout: AMemorixWebImportTimeoutConfig = Field(
        default_factory=AMemorixWebImportTimeoutConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "导入超时",
                "en_US": "Import timeouts",
                "ja_JP": "インポートタイムアウト",
            },
        },
    )
    """导入中心超时配置"""


class AMemorixWebTuningConfig(ConfigBase):
    """A_Memorix 调优中心配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用调优中心",
                "en_US": "Enable tuning center",
                "ja_JP": "チューニングセンターを有効化",
            },
        },
    )
    """是否启用调优中心"""

    max_queue_size: int = Field(
        default=8,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大队列",
                "en_US": "Max queue size",
                "ja_JP": "最大キューサイズ",
            },
        },
    )
    """最大队列长度"""

    poll_interval_ms: int = Field(
        default=1200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "轮询间隔",
                "en_US": "Poll interval",
                "ja_JP": "ポーリング間隔",
            },
        },
    )
    """轮询间隔毫秒数"""

    default_intensity: Literal["quick", "standard", "deep"] = Field(
        default="standard",
        json_schema_extra={
            "label": {
                "zh_CN": "默认强度",
                "en_US": "Default intensity",
                "ja_JP": "既定の強度",
            },
        },
    )
    """默认调优强度"""

    default_objective: Literal["precision_priority", "balanced", "recall_priority"] = Field(
        default="precision_priority",
        json_schema_extra={
            "label": {
                "zh_CN": "默认目标",
                "en_US": "Default objective",
                "ja_JP": "既定の目標",
            },
        },
    )
    """默认调优目标"""

    default_top_k_eval: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认评估 Top-K",
                "en_US": "Default eval Top-K",
                "ja_JP": "既定評価 Top-K",
            },
        },
    )
    """默认评估 Top-K"""

    default_sample_size: int = Field(
        default=24,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认样本数",
                "en_US": "Default sample size",
                "ja_JP": "既定サンプル数",
            },
        },
    )
    """默认样本数"""


class AMemorixWebConfig(ConfigBase):
    """A_Memorix Web 运维配置"""

    import_config: AMemorixWebImportConfig = Field(
        default_factory=AMemorixWebImportConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "导入中心",
                "en_US": "Import center",
                "ja_JP": "インポートセンター",
            },
        },
    )
    """导入中心配置"""

    tuning: AMemorixWebTuningConfig = Field(
        default_factory=AMemorixWebTuningConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "调优中心",
                "en_US": "Tuning center",
                "ja_JP": "チューニングセンター",
            },
        },
    )
    """调优中心配置"""


class AMemorixConfig(ConfigBase):
    """长期记忆配置"""

    __ui_label__ = "记忆"
    __ui_order__ = 50

    plugin: AMemorixPluginConfig = Field(
        default_factory=AMemorixPluginConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆系统",
                "en_US": "Memory system",
                "ja_JP": "記憶システム",
            },
        },
    )
    """长期记忆系统的总开关"""

    integration: AMemorixIntegrationConfig = Field(
        default_factory=AMemorixIntegrationConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天中使用记忆",
                "en_US": "Use memory in chat",
                "ja_JP": "MaiSaka 連携",
            },
        },
    )
    """控制麦麦在聊天中如何使用长期记忆"""

    storage: AMemorixStorageConfig = Field(
        default_factory=AMemorixStorageConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "存储",
                "en_US": "Storage",
                "ja_JP": "ストレージ",
            },
        },
    )
    """存储位置"""

    image_memory: AMemorixImageMemoryConfig = Field(
        default_factory=AMemorixImageMemoryConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "图片记忆",
                "en_US": "Image memory",
                "ja_JP": "画像記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """图片资产留存、图片嵌入和相似内容召回配置"""

    embedding: AMemorixEmbeddingConfig = Field(
        default_factory=AMemorixEmbeddingConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆向量化",
                "en_US": "Memory embedding",
                "ja_JP": "記憶ベクトル化",
            },
            "x-collapsed-by-default": True,
        },
    )
    """把记忆内容转换为向量时使用的基础设置"""

    retrieval: AMemorixRetrievalConfig = Field(
        default_factory=AMemorixRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "检索",
                "en_US": "Retrieval",
                "ja_JP": "検索",
            },
        },
    )
    """检索配置"""

    threshold: AMemorixThresholdConfig = Field(
        default_factory=AMemorixThresholdConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "阈值",
                "en_US": "Thresholds",
                "ja_JP": "しきい値",
            },
        },
    )
    """阈值过滤配置"""

    filter: AMemorixFilterConfig = Field(
        default_factory=AMemorixFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "过滤",
                "en_US": "Filter",
                "ja_JP": "フィルター",
            },
        },
    )
    """聊天过滤配置"""

    global_memory_sharing_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "全局共享记忆",
                "en_US": "Global memory sharing",
                "ja_JP": "記憶のグローバル共有",
            },
            "x-widget": "switch",
        },
    )
    """是否让普通记忆查询在所有聊天流范围内检索"""

    shared_memory_groups: list[ChatStreamGroup] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "共享记忆组",
                "en_US": "Shared memory groups",
                "ja_JP": "共有記憶グループ",
            },
            "x-widget": "custom",
            "x-display-as-section": True,
        },
    )
    """把需要互相参考长期记忆的群聊或私聊放到同一组"""

    episode: AMemorixEpisodeConfig = Field(
        default_factory=AMemorixEpisodeConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Episode",
                "en_US": "Episodes",
                "ja_JP": "Episode",
            },
        },
    )
    """Episode 配置"""

    person_profile: AMemorixPersonProfileConfig = Field(
        default_factory=AMemorixPersonProfileConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "人物画像",
                "en_US": "Person profiles",
                "ja_JP": "人物プロファイル",
            },
        },
    )
    """人物画像配置"""

    memory: AMemorixMemoryEvolutionConfig = Field(
        default_factory=AMemorixMemoryEvolutionConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆演化",
                "en_US": "Memory evolution",
                "ja_JP": "記憶進化",
            },
        },
    )
    """记忆演化配置"""

    advanced: AMemorixAdvancedConfig = Field(
        default_factory=AMemorixAdvancedConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "高级",
                "en_US": "Advanced",
                "ja_JP": "詳細設定",
            },
        },
    )
    """高级运行时配置"""

    web: AMemorixWebConfig = Field(
        default_factory=AMemorixWebConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Web 运维",
                "en_US": "Web operations",
                "ja_JP": "Web 運用",
            },
        },
    )
    """Web 运维配置"""


