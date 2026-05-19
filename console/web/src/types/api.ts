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
}
