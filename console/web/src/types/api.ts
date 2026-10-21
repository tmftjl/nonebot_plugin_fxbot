// -*- coding: utf-8 -*-
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  message?: string
  errors?: Record<string, string>
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
}
