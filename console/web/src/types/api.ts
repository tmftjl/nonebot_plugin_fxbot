// -*- coding: utf-8 -*-
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  message?: string
  errors?: Record<string, string>
}

export interface CacheDomain {
  domain: string
  entries: number
  hits: number
  misses: number
  evictions: number
  expired: number
  sets: number
  bytes: number
  hit_rate: number
}

export interface CacheStats {
  process: {
    rss: number | null
    cache_bytes: number
    entries: number
  }
  domains: CacheDomain[]
}

export interface CacheKey {
  key: string
  bytes: number
  ttl: number | null
  created_at: number
  accessed_at: number
}

export interface CacheKeys {
  domain: string
  total: number
  keys: CacheKey[]
}

export type CacheClearPayload =
  | { scope: 'all' }
  | { scope: 'domain'; domain: string }
  | { scope: 'key'; key: string }

export interface CacheClearResult {
  scope: CacheClearPayload['scope']
  domain?: string
  key?: string
  removed: number | null
}

export interface PluginInfo {
  name: string
  display_name: string
  category: string
  enabled: boolean
}

export interface MembershipInfo {
  group_id: string
  group_name: string
  expire_time: string
  days_left: number
  status: 'active' | 'expired' | 'trial'
}

export interface MessageStats {
  plugin: string
  count: number
  last_used: string
}

export interface PermissionConfig {
  top: LayerConfig
  sub_plugins: Record<string, PluginPermission>
}

export interface LayerConfig {
  enabled: boolean
  whitelist?: {
    users: string[]
    groups: string[]
  }
  blacklist?: {
    users: string[]
    groups: string[]
  }
  scene: 'all' | 'group' | 'private'
  level: 'superuser' | 'bot_admin' | 'owner' | 'admin' | 'member' | 'all'
}

export interface PluginPermission {
  top: LayerConfig
  commands: Record<string, LayerConfig>
}

export interface GroupRecord {
  id?: number
  gid: string
  expiry: string
  days: number
  status: 'active' | 'soon' | 'today' | 'expired'
  managed_by_bot?: string
  last_renewed_by?: string
  remark?: string
}

export interface RenewalCode {
  code: string
  length: number
  unit: string
  max_use: number
  used_count: number
  generated_time: string
  expire_at?: string
}

export interface StatsToday {
  bots: Record<string, BotStats>
}

export interface StatsHistoryDay {
  date: string
  sent: number
  group: number
  private: number
}

export interface StatsHistory {
  days: number
  items: StatsHistoryDay[]
}

export interface ModelUsageModel {
  model_name: string
  provider_name: string
  request_count: number
  failure_count: number
  avg_duration: number | null
  failure_rate: number
}

export interface ModelUsageTask {
  task_name: string
  request_count: number
  failure_count: number
  avg_duration: number | null
  failure_rate: number
  models: ModelUsageModel[]
}

export interface ModelUsage {
  hours: number
  request_count: number
  tasks: ModelUsageTask[]
}

export interface SystemStatus {
  available: boolean
  cpu?: {
    percent: number | null
    count: number
    physical: number
    freq_mhz: number | null
    history: number[]
  }
  memory?: {
    percent: number | null
    used: number
    total: number
    history: number[]
  }
  swap?: { percent: number | null; used: number; total: number }
  disks?: Array<{ mount: string; percent: number | null; used: number; total: number }>
  network?: {
    upload: number
    download: number
    total_upload: number
    total_download: number
  }
  process?: { rss: number; threads: number; uptime: number }
  system?: {
    platform: string
    release: string
    machine: string
    python: string
    uptime: number
  }
}

export interface BotStats {
  total_sent: number
  group?: {
    count: number
    targets: Record<string, number>
  }
  private?: {
    count: number
    targets: Record<string, number>
  }
}

export interface Persona {
  key: string
  details: string
  knowledgeCount?: number
}

export interface ProviderTypeOption {
  type: string
  provider_type: 'chat' | 'embedding' | string
  label: string
  default_base_url?: string
  default_timeout?: number
  default_embedding_dim?: number | null
  default_voice?: string | null
}

export interface ProviderItem {
  name: string
  type: string
  type_label: string
  provider_type: 'chat' | 'embedding' | string
  base_url: string
  model: string
  timeout: number
  embedding_dim?: number | null
  voice?: string | null
  has_api_key: boolean
  is_default: boolean
}

export interface ProviderListData {
  providers: ProviderItem[]
  types: ProviderTypeOption[]
  default_provider: string
}

export interface ProviderPayload {
  name?: string
  type: string
  provider_type: string
  api_key?: string
  base_url: string
  model: string
  timeout: number
  embedding_dim?: number | null
  voice?: string | null
}

export interface ModelRoutingProvider {
  name: string
  model: string
  type_label: string
  provider_type: string
}

export interface ModelRoutingTask {
  task: string
  label: string
  description: string
  provider: string
  provider_type: string
  model: string
  configured: boolean
}

export interface ModelRoutingData {
  tasks: ModelRoutingTask[]
  providers: ModelRoutingProvider[]
  default_provider: string
}

export interface ChatTenant {
  tenant: string
  messages: number
  learn_count?: number
  use_count?: number
}

export interface ChatStyleExample {
  text: string
  seq: number
}

export interface ChatStyle {
  tenant: string
  summary: string
  manual_override: string
  rules: string[]
  examples: ChatStyleExample[]
  version: number
  learn_count: number
  use_count: number
  evidence_seq_start: number
  evidence_seq_end: number
  evidence_count: number
  last_used_at: string | null
  updated_at: string | null
}

export interface ChatJargon {
  id: number
  term: string
  meaning: string
  status: 'active' | 'rejected' | string
  count: number
  learn_count: number
  use_count: number
  first_seen: string | null
  last_seen: string | null
  last_used_at: string | null
}

export interface ChatExpression {
  id: number
  situation: string
  style: string
  type: string
  count: number
  checked: boolean
  rejected: boolean
  learn_count: number
  use_count: number
  source_message_seq: number
  last_used_at: string | null
  updated_at: string | null
}

export interface ChatPlannerSample {
  id: number
  turn_id: string
  trigger_kind: string
  action: string
  label: number
  features: Record<string, unknown>
  provider: string
  model: string
  created_at: string | null
}

export interface ChatReplyGateWindow {
  window_seconds: number
  scored_rounds: number
  expected_proactive: number
  expected_forced: number
  expected_replies: number
  actual_replies: number
}

export interface ChatReplyGateObservation {
  occurred_at: string
  probability: number
  threshold: number
  would_trigger: boolean
  expected_replies: number
  target_replies: number
  actual_replies: number
  keep_ratio: number
  features: Record<string, unknown>
}

export interface ChatReplyGateBucket {
  bucket: string
  count: number
}

export interface ChatReplyGateBackoff {
  pending: number
  delay_seconds: number
}

export interface ChatReplyGateProactive {
  talk_value: number
  switch: boolean | null
}

export interface ChatReplyGate {
  calibrated: boolean
  window: ChatReplyGateWindow
  backoff: ChatReplyGateBackoff
  observations: ChatReplyGateObservation[]
  distribution: ChatReplyGateBucket[]
  proactive: ChatReplyGateProactive
}

export type ChatEmojiStatus = 'registered' | 'pending' | 'unidentified' | 'disabled'

export interface ChatEmoji {
  hash: string
  description: string
  tags: string[]
  count: number
  last_used: string | null
  enabled: boolean
  registered: boolean
  vlm_processed: boolean
  status: ChatEmojiStatus
  register_time: string | null
  created_at: string | null
}

export interface ChatEmojiCounts {
  registered: number
  pending: number
  unidentified: number
  disabled: number
  all: number
}

export interface ChatHealth {
  enabled: boolean
  storage_ready: boolean
  degraded_reason: string
  scheduler_ready: boolean
  online_bots: number
}

export interface ChatTraceStage {
  kind: 'planner' | 'replyer' | 'send'
  label: string
  duration_ms: number
  provider: string
  error?: string
  target_message_id?: string
  budget?: Record<string, any>
  injected?: Record<string, any>
  send?: {
    sent_parts: number
    total_parts: number
    failed_index: number | null
    cancelled: boolean
    send_ms: number
  }
}

export interface ChatTrace {
  id: number
  turn_id: string
  trigger_kind: string
  request_preview: string
  injected: Record<string, any>
  budget: Record<string, any>
  provider: string
  latency_ms: number
  error: string
  created_at: string | null
}

// A_Memorix 长期记忆
export type AmemorixRecordType = 'paragraph' | 'entity' | 'relation' | 'episode' | 'fact'

export interface AmemorixRecord {
  type: AmemorixRecordType
  id: string
  title: string
  summary: string
  source: string
  source_label: string
  status: string
  created_at: string | null
  updated_at: string | null
  metadata: Record<string, any>
}

export interface AmemorixRecords {
  success: boolean
  query: string
  types: AmemorixRecordType[]
  count: number
  counts: Record<string, number>
  items: AmemorixRecord[]
}

export interface AmemorixRecordDetail {
  success: boolean
  record: AmemorixRecord
  detail: Record<string, any>
  paragraphs: AmemorixParagraph[]
}

export interface AmemorixParagraph {
  hash: string
  content: string
  source: string
  created_at: string | null
}

export interface AmemorixStatus {
  available: boolean
  enabled: boolean
  error: string
  counts: {
    paragraphs: number
    entities: number
    relations: number
    episodes: number
    facts: number
    persons: number
    profiles: number
  }
  runtime: Record<string, any>
  integration: Record<string, boolean>
}

export interface AmemorixSource {
  source: string
  source_label: string
  count: number
  last_updated: string | null
}

export interface AmemorixProfile {
  person_id: string
  name: string
  user_id: string
  platform: string
  profile_text: string
  profile_version: number
  updated_at: string | null
  expires_at: string | null
  source_note: string
  fact_count: number
}

export interface AmemorixProfileDetail {
  person: Record<string, any>
  profile: {
    profile_version: number
    profile_text: string
    updated_at: string | null
    expires_at: string | null
    source_note: string
    aliases: string[]
    alias_override: boolean
  }
  facts: AmemorixRecord[]
}

export interface AmemorixGraphNode {
  id: string
  name: string
  hash: string
  appearance_count: number
}

export interface AmemorixGraphEdge {
  source: string
  target: string
  predicates: string[]
  relation_hashes: string[]
  confidence: number
  label: string
  relation_count: number
}

export interface AmemorixGraph {
  nodes: AmemorixGraphNode[]
  edges: AmemorixGraphEdge[]
  total_nodes: number
  total_edges: number
}

export interface AmemorixGraphNodeDetail {
  node: Record<string, any>
  relations: Array<Record<string, any>>
  paragraphs: AmemorixParagraph[]
}

export interface AmemorixRuntime {
  available: boolean
  error?: string
  [key: string]: any
}

export interface AmemorixConfig {
  config: Record<string, any>
  restart_required?: boolean
}

export interface AmemorixTimelineItem {
  id: string
  category: string
  event_type: string
  title: string
  summary: string
  timestamp: string | null
  target: { type: string; id: string }
}

export interface AmemorixRecycleItem {
  id: string
  kind: 'operation' | 'fact'
  mode: string
  title: string
  summary: string
  count: number
  status: string
  created_at: string | null
  restored_at: string | null
  preview: Array<{ type: string; label: string }>
}

export interface AmemorixCorrectionItem {
  id: string
  kind: 'feedback' | 'plan'
  title: string
  status: string
  decision: string
  rollback_status: string
  created_at: string | null
  updated_at: string | null
  detail: Record<string, any>
}

export interface AmemorixCorrectionTarget {
  type: 'paragraph' | 'relation'
  id: string
}

export interface AmemorixCorrectionOperation {
  action: string
  target_type?: string
  hash?: string
  text?: string
  reason?: string
  [key: string]: any
}

export interface AmemorixCorrectionPreview {
  success: boolean
  plan_id: string
  plan: Record<string, any>
  preview: {
    request_text: string
    scope: string
    candidates: Array<Record<string, any>>
    operations: AmemorixCorrectionOperation[]
    cascade_preview?: Record<string, any>
    reason?: string
    [key: string]: any
  }
  requires_confirmation?: boolean
}

export interface AmemorixBundle {
  installation_id: string
  package_id: string
  version: string
  name: string
  description: string
  author: string
  scope_type: string
  chat_id: string
  status: string
  paragraph_count: number
  images: Record<string, any>
  updated_at: string | null
  [key: string]: any
}

export interface AmemorixImportTask {
  task_id: string
  source: string
  status: string
  current_step: string
  files?: Array<Record<string, any>>
  [key: string]: any
}

export interface AmemorixImportSettings {
  max_files_per_task: number
  max_file_size_mb: number
  max_paste_chars: number
  [key: string]: any
}

export interface AmemorixImage {
  asset_id: string
  occurrence_id: string
  content_hash: string
  mime_type: string
  byte_size: number
  width: number
  height: number
  status: string
  occurrence_count: number
  latest_occurrence_at: number | null
}

export interface AmemorixImageStatus {
  status: string
  message?: string
  dimension?: number
  stats?: Record<string, number>
  [key: string]: any
}
