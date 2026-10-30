// -*- coding: utf-8 -*-
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import type {
  ApiResponse,
  ProviderListData,
  ProviderPayload,
  PermissionConfig,
  StatsToday,
  StatsHistory,
  ModelUsage,
  SystemStatus,
  CacheStats,
  CacheKeys,
  CacheClearPayload,
  CacheClearResult,
  ChatTenant,
  ChatStyle,
  ChatJargon,
  ChatExpression,
  ChatPlannerSample,
  ChatReplyGate,
  ChatEmoji,
  ChatEmojiCounts,
  ChatHealth,
  ChatTrace,
  AmemorixConfig,
  AmemorixBundle,
  AmemorixCorrectionItem,
  AmemorixCorrectionPreview,
  AmemorixCorrectionTarget,
  AmemorixGraph,
  AmemorixGraphNodeDetail,
  AmemorixImage,
  AmemorixImageDetail,
  AmemorixImageStatus,
  AmemorixImportSettings,
  AmemorixImportTask,
  AmemorixProfile,
  AmemorixProfileDetail,
  AmemorixRecordDetail,
  AmemorixRecords,
  AmemorixRecycleItem,
  AmemorixRuntime,
  AmemorixSource,
  AmemorixStatus,
  AmemorixTimelineItem,
  ModelRoutingData,
} from '@/types/api'

import { clearToken, getToken } from './auth'

export { getToken }

const tenantQuery = (tenant: string) => `tenant=${encodeURIComponent(tenant)}`

class ApiClient {
  private client: AxiosInstance

  constructor() {
    this.client = axios.create({
      baseURL: '/fxbot',
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json'
      }
    })

    this.client.interceptors.request.use((config) => {
      const token = getToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
      return config
    })

    this.client.interceptors.response.use(
      (response) => response,
      (error) => {
        const status = error?.response?.status
        if (status === 401 || status === 403) {
          // 令牌失效：清掉本地凭证，界面回到登录页
          clearToken('登录已过期，请重新登录')
        }
        console.error('API Error:', error)
        return Promise.reject(error)
      }
    )
  }

  async get<T = any>(url: string): Promise<T> {
    const response: AxiosResponse<T> = await this.client.get(url)
    return response.data
  }

  async post<T = any>(url: string, data?: any): Promise<T> {
    const response: AxiosResponse<T> = await this.client.post(url, data)
    return response.data
  }

  async put<T = any>(url: string, data?: any): Promise<T> {
    const response: AxiosResponse<T> = await this.client.put(url, data)
    return response.data
  }

  async patch<T = any>(url: string, data?: any): Promise<T> {
    const response: AxiosResponse<T> = await this.client.patch(url, data)
    return response.data
  }

  async delete<T = any>(url: string): Promise<T> {
    const response: AxiosResponse<T> = await this.client.delete(url)
    return response.data
  }
}

export const api = new ApiClient()

// 数据 API
export const dataApi = {
  getAll: () => api.get<Record<string, any>>('/membership/data'),
}

// 配置 API
let configRevisions: Record<string, string> | undefined

export const configApi = {
  getAll: async () => {
    const { revisions, ...configs } = await api.get<Record<string, any>>('/config')
    configRevisions = revisions
    return configs
  },
  getTabs: () => api.get<any[]>('/config/tabs'),
  getChatTab: () => api.get<any>('/config/chat'),
  save: (configs: Record<string, any>) => api.put<ApiResponse>('/config', {
    ...configs,
    base_revisions: configRevisions,
  }),
}

// 权限 API
export const permissionApi = {
  get: () => api.get<PermissionConfig>('/permissions'),
  save: (config: PermissionConfig) => api.put<ApiResponse>('/permissions', config),
}

// 统计 API
export const statsApi = {
  getToday: () => api.get<StatsToday>('/stats/today'),
  getHistory: (days = 14) => api.get<StatsHistory>(`/stats/history?days=${days}`),
  getModelUsage: (hours = 24) => api.get<ModelUsage>(`/stats/model_usage?hours=${hours}`),
}

export const systemApi = {
  status: () => api.get<SystemStatus>('/system/status'),
}

export const cacheApi = {
  stats: () => api.get<CacheStats>('/cache'),
  keys: (domain: string, limit = 200) =>
    api.get<CacheKeys>(`/cache/keys?domain=${encodeURIComponent(domain)}&limit=${limit}`),
  clear: (payload: CacheClearPayload) => api.post<CacheClearResult>('/cache/clear', payload),
}

// 聊天记忆 API
export const chatApi = {
  tenants: () => api.get<{ tenants: ChatTenant[] }>('/chat/tenants'),
  getStyle: (tenant: string) =>
    api.get<{ style: ChatStyle | null }>(`/chat/style?tenant=${encodeURIComponent(tenant)}`),
  saveStyle: (tenant: string, manualOverride: string) =>
    api.put<{ style: ChatStyle }>('/chat/style', { tenant, manual_override: manualOverride }),
  deleteStyle: (tenant: string) =>
    api.delete<{ deleted: boolean }>(`/chat/style?tenant=${encodeURIComponent(tenant)}`),
  listJargon: (tenant: string, status = 'all') =>
    api.get<{ items: ChatJargon[] }>(
      `/chat/jargon?tenant=${encodeURIComponent(tenant)}&status=${encodeURIComponent(status)}`,
    ),
  deleteJargon: (tenant: string, id: number) =>
    api.delete<{ deleted: boolean }>(`/chat/jargon/${id}?tenant=${encodeURIComponent(tenant)}`),
  listExpressions: (tenant: string, status = 'all') =>
    api.get<{ items: ChatExpression[] }>(
      `/chat/expression?tenant=${encodeURIComponent(tenant)}&status=${encodeURIComponent(status)}`,
    ),
  reviewExpression: (tenant: string, id: number, rejected: boolean) =>
    api.patch<{ rejected: boolean }>(
      `/chat/expression/${id}?tenant=${encodeURIComponent(tenant)}&rejected=${rejected}`,
    ),
  deleteExpression: (tenant: string, id: number) =>
    api.delete<{ deleted: boolean }>(`/chat/expression/${id}?tenant=${encodeURIComponent(tenant)}`),
  listPlannerSamples: (tenant: string, limit = 100) =>
    api.get<{ items: ChatPlannerSample[] }>(
      `/chat/planner?tenant=${encodeURIComponent(tenant)}&limit=${limit}`,
    ),
  getReplyGate: (tenant: string, limit = 100) =>
    api.get<ChatReplyGate>(`/chat/gate?tenant=${encodeURIComponent(tenant)}&limit=${limit}`),
  listEmoji: (
    tenant: string,
    options: { status?: string; search?: string; format?: string; sort?: string; order?: string } = {},
  ) => {
    const params = new URLSearchParams({ tenant })
    if (options.status) params.set('status', options.status)
    if (options.search) params.set('search', options.search)
    if (options.format) params.set('format', options.format)
    if (options.sort) params.set('sort', options.sort)
    if (options.order) params.set('order', options.order)
    return api.get<{ items: ChatEmoji[]; counts: ChatEmojiCounts }>(`/chat/emoji?${params.toString()}`)
  },
  uploadEmoji: (tenant: string, content: string, description = '', tags: string[] = []) =>
    api.post<{ item: ChatEmoji; warning?: string }>('/chat/emoji', { tenant, content, description, tags }),
  updateEmoji: (
    tenant: string,
    hash: string,
    patch: { enabled?: boolean; registered?: boolean; description?: string; tags?: string[] },
  ) =>
    api.patch<{ item: ChatEmoji }>(
      `/chat/emoji/${encodeURIComponent(hash)}?tenant=${encodeURIComponent(tenant)}`,
      patch,
    ),
  deleteEmoji: (tenant: string, hash: string) =>
    api.delete<{ deleted: boolean }>(
      `/chat/emoji/${encodeURIComponent(hash)}?tenant=${encodeURIComponent(tenant)}`,
    ),
  batchDeleteEmoji: (tenant: string, hashes: string[]) =>
    api.post<{ deleted: number }>('/chat/emoji/batch-delete', { tenant, hashes }),
  emojiThumbnailUrl: (tenant: string, hash: string) =>
    `/fxbot/chat/emoji/${encodeURIComponent(hash)}/thumbnail?tenant=${encodeURIComponent(tenant)}&token=${encodeURIComponent(getToken())}`,
  emojiImageUrl: (tenant: string, hash: string) =>
    `/fxbot/chat/emoji/${encodeURIComponent(hash)}/image?tenant=${encodeURIComponent(tenant)}&token=${encodeURIComponent(getToken())}`,
  getHealth: () => api.get<ChatHealth>('/chat/health'),
  listTraces: (tenant: string, limit = 100) =>
    api.get<{ items: ChatTrace[] }>(
      `/chat/trace?tenant=${encodeURIComponent(tenant)}&limit=${limit}`,
    ),
}

// A_Memorix 长期记忆 API
export const amemorixApi = {
  status: (tenant: string) => api.get<AmemorixStatus>(`/amemorix/status?${tenantQuery(tenant)}`),
  records: (
    tenant: string,
    options: { query?: string; types?: string; limit?: number; includeInactive?: boolean } = {},
  ) => {
    const params = new URLSearchParams({ tenant })
    if (options.query) params.set('query', options.query)
    if (options.types) params.set('types', options.types)
    if (options.limit) params.set('limit', String(options.limit))
    if (options.includeInactive) params.set('include_inactive', 'true')
    return api.get<AmemorixRecords>(`/amemorix/records?${params.toString()}`)
  },
  record: (tenant: string, type: string, id: string) =>
    api.get<AmemorixRecordDetail>(
      `/amemorix/records/${encodeURIComponent(type)}/${encodeURIComponent(id)}?${tenantQuery(tenant)}`,
    ),
  deleteRecord: (tenant: string, type: string, id: string) =>
    api.delete<{ deleted: boolean }>(
      `/amemorix/records/${encodeURIComponent(type)}/${encodeURIComponent(id)}?${tenantQuery(tenant)}`,
    ),
  sources: (tenant: string) => api.get<{ items: AmemorixSource[] }>(`/amemorix/sources?${tenantQuery(tenant)}`),
  episodes: (tenant: string, query = '') =>
    api.get<{ items: AmemorixRecords['items'] }>(
      `/amemorix/episodes?${tenantQuery(tenant)}&query=${encodeURIComponent(query)}`,
    ),
  profiles: (tenant: string, query = '') =>
    api.get<{ items: AmemorixProfile[] }>(
      `/amemorix/profiles?${tenantQuery(tenant)}&query=${encodeURIComponent(query)}`,
    ),
  profile: (tenant: string, personId: string) =>
    api.get<AmemorixProfileDetail>(
      `/amemorix/profiles/${encodeURIComponent(personId)}?${tenantQuery(tenant)}`,
    ),
  graph: (tenant: string, limit = 200) =>
    api.get<AmemorixGraph>(`/amemorix/graph?${tenantQuery(tenant)}&limit=${limit}`),
  graphNode: (tenant: string, node: string) =>
    api.get<AmemorixGraphNodeDetail>(
      `/amemorix/graph/node?${tenantQuery(tenant)}&node=${encodeURIComponent(node)}`,
    ),
  runtime: () => api.get<AmemorixRuntime>('/amemorix/runtime'),
  selfCheck: () => api.post<{ success: boolean; report: Record<string, any> }>('/amemorix/runtime/self-check'),
  getConfig: () => api.get<AmemorixConfig>('/amemorix/config'),
  saveConfig: (config: Record<string, any>) => api.put<AmemorixConfig>('/amemorix/config', { config }),
  timeline: (tenant: string, limit = 100) =>
    api.get<{ items: AmemorixTimelineItem[] }>(
      `/amemorix/timeline?${tenantQuery(tenant)}&limit=${limit}`,
    ),
  recycle: (tenant: string, limit = 50) =>
    api.get<{ items: AmemorixRecycleItem[] }>(`/amemorix/recycle?${tenantQuery(tenant)}&limit=${limit}`),
  restoreRecycle: (payload: { tenant: string; kind: string; id: string }) =>
    api.post<{ success: boolean }>('/amemorix/recycle/restore', payload),
  purgeRecycle: (tenant: string, graceHours = 24) =>
    api.post<{ success: boolean; result: Record<string, any> }>('/amemorix/recycle/purge', {
      tenant,
      grace_hours: graceHours,
    }),
  corrections: (tenant: string, kind = '', limit = 50) =>
    api.get<{ items: AmemorixCorrectionItem[] }>(
      `/amemorix/corrections?${tenantQuery(tenant)}&kind=${encodeURIComponent(kind)}&limit=${limit}`,
    ),
  rollbackCorrection: (payload: { tenant: string; kind: string; id: string }) =>
    api.post<{ success: boolean }>('/amemorix/corrections/rollback', payload),
  previewCorrection: (payload: {
    tenant: string
    request_text: string
    targets: AmemorixCorrectionTarget[]
  }) => api.post<AmemorixCorrectionPreview>('/amemorix/corrections/preview', payload),
  executeCorrection: (payload: { tenant: string; plan_id: string }) =>
    api.post<{ success: boolean; plan_id?: string }>('/amemorix/corrections/execute', payload),
  bundles: (limit = 50) => api.get<{ items: AmemorixBundle[] }>(`/amemorix/bundles?limit=${limit}`),
  inspectBundle: (path: string) =>
    api.get<Record<string, any>>(`/amemorix/bundles/inspect?path=${encodeURIComponent(path)}`),
  exportBundle: (tenant = '', contentLevel = 'knowledge') =>
    api.post<Record<string, any>>('/amemorix/bundles/export', { tenant, content_level: contentLevel }),
  importBundle: (payload: {
    file_name: string
    content_base64: string
    tenant?: string
    mode?: string
  }) => api.post<Record<string, any>>('/amemorix/bundles/import', payload),
  uninstallBundle: (installationId: string) =>
    api.post<Record<string, any>>('/amemorix/bundles/uninstall', { installation_id: installationId }),
  bundleFileUrl: (fileName: string) =>
    `/fxbot/amemorix/bundles/file?file_name=${encodeURIComponent(fileName)}&token=${encodeURIComponent(getToken())}`,
  imports: (limit = 50) =>
    api.get<{ items: AmemorixImportTask[]; settings: AmemorixImportSettings }>(
      `/amemorix/imports?limit=${limit}`,
    ),
  importSettings: () => api.get<{ success: boolean; settings: AmemorixImportSettings }>('/amemorix/imports/settings'),
  importTask: (taskId: string) =>
    api.get<{ success: boolean; task: AmemorixImportTask }>(
      `/amemorix/imports/${encodeURIComponent(taskId)}?chunks=true`,
    ),
  pasteImport: (payload: {
    tenant: string
    name?: string
    content: string
    input_mode?: string
    llm_enabled?: boolean
  }) => api.post<Record<string, any>>('/amemorix/imports/paste', payload),
  uploadImport: (payload: {
    tenant: string
    file_name: string
    content_base64: string
    input_mode?: string
    llm_enabled?: boolean
  }) => api.post<Record<string, any>>('/amemorix/imports/upload', payload),
  cancelImport: (taskId: string) =>
    api.post<Record<string, any>>(`/amemorix/imports/${encodeURIComponent(taskId)}/cancel`),
  retryImport: (taskId: string) =>
    api.post<Record<string, any>>(`/amemorix/imports/${encodeURIComponent(taskId)}/retry`),
  images: (tenant: string, limit = 50, offset = 0) =>
    api.get<{ success: boolean; items: AmemorixImage[]; status?: string; error?: string }>(
      `/amemorix/images?${tenantQuery(tenant)}&limit=${limit}&offset=${offset}`,
    ),
  imageStatus: () => api.get<AmemorixImageStatus>('/amemorix/images/status'),
  retryImages: () => api.post<Record<string, any>>('/amemorix/images/retry'),
  uploadImage: (payload: { tenant: string; content_base64: string; description?: string }) =>
    api.post<Record<string, any>>('/amemorix/images', payload),
  imageDetail: (tenant: string, assetId: string) =>
    api.get<AmemorixImageDetail>(`/amemorix/images/${encodeURIComponent(assetId)}/detail?${tenantQuery(tenant)}`),
  deleteImage: (tenant: string, occurrenceId: string) =>
    api.delete<Record<string, any>>(
      `/amemorix/images/${encodeURIComponent(occurrenceId)}?${tenantQuery(tenant)}`,
    ),
  imageUrl: (tenant: string, occurrenceId: string) =>
    `/fxbot/amemorix/images/${encodeURIComponent(occurrenceId)}/file?${tenantQuery(tenant)}&token=${encodeURIComponent(getToken())}`,
}

// 会员续费 API
export const renewalApi = {
  getCodes: () => api.get<Record<string, any>>('/membership/codes'),
  generateCode: (length: number, unit: string, maxUse?: number, expireDays?: number) =>
    api.post<{ code: string }>('/membership/generate', { length, unit, max_use: maxUse, expire_days: expireDays }),
  extend: (payload: {
    id?: number
    group_id?: string | number
    length?: number
    unit?: string
    expiry?: string
    managed_by_bot?: string
    renewed_by?: string
    remark?: string
  }) => api.post<{ group_id: string; expiry: string; id?: number }>('/membership/extend', payload),
  remind: (groupId: number) =>
    api.post<{ sent: number }>('/membership/remind', { group_id: groupId }),
  deleteRecord: (groupId: number) =>
    api.delete<{ deleted: number }>(`/membership/groups/${groupId}`),
  leave: (groupId: number) =>
    api.post<{ left: number }>('/membership/leave', { group_id: groupId }),
  notify: (groupIds: number[], text: string, images?: string[]) =>
    api.post<{ sent: number }>('/membership/notify', { group_ids: groupIds, text, images }),
  runJob: () => api.post<{ expired: number; left: number }>('/membership/job/run'),
}

// AI Provider API
export const providerApi = {
  list: () => api.get<ApiResponse<ProviderListData>>('/ai_chat/providers'),
  create: (payload: ProviderPayload) => api.post<ApiResponse>('/ai_chat/provider', payload),
  update: (name: string, payload: ProviderPayload) =>
    api.put<ApiResponse>(`/ai_chat/provider/${encodeURIComponent(name)}`, payload),
  remove: (name: string) => api.delete<ApiResponse>(`/ai_chat/provider/${encodeURIComponent(name)}`),
  setDefault: (name: string) => api.post<ApiResponse>(`/ai_chat/provider/${encodeURIComponent(name)}/default`),
  routing: () => api.get<ApiResponse<ModelRoutingData>>('/ai_chat/routing'),
  setRouting: (task: string, provider: string) =>
    api.put<ApiResponse>(`/ai_chat/routing/${encodeURIComponent(task)}`, { provider }),
}

// 插件/命令显示名 API
export const metaApi = {
  getPlugins: () => api.get<Record<string, string>>('/plugins'),
  getCommands: () => api.get<Record<string, Record<string, string>>>('/commands'),
  getBots: async () => {
    const data = await api.get<{ bots: Array<string | { self_id: string }> }>('/bots')
    return {
      bots: (data.bots || []).map((item) => typeof item === 'string' ? item : item.self_id)
    }
  },
  getPersonas: () =>
    api.get<{ items: Record<string, string>; default: string }>('/ai_chat/personas'),
  createPersona: (key: string, desc: string) =>
    api.post<{ success: boolean; message?: string }>('/ai_chat/persona', { key, desc }),
  updatePersona: (key: string, desc: string) =>
    api.put<{ success: boolean; message?: string }>(`/ai_chat/persona/${encodeURIComponent(key)}`, { desc }),
  deletePersona: (key: string) =>
    api.delete<{ success: boolean; message?: string }>(`/ai_chat/persona/${encodeURIComponent(key)}`),
  getKnowledgeStats: (personaName: string) => api.get<{ persona_name: string; count: number }>(`/ai_chat/knowledge/${encodeURIComponent(personaName)}/stats`),
  importKnowledgeText: (personaName: string, text: string, source: string = 'webui_import') =>
    api.post<{ success: boolean; message: string; count: number }>(`/ai_chat/knowledge/${encodeURIComponent(personaName)}/text`, { text, source }),
  clearKnowledge: (personaName: string) =>
    api.delete<{ success: boolean; message: string }>(`/ai_chat/knowledge/${encodeURIComponent(personaName)}`),
  getTools: () => api.get<{ success: boolean; data: Array<{ label: string; value: string }> }>('/ai_chat/tools'),
}
