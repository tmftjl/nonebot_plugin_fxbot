// -*- coding: utf-8 -*-
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import type { ApiResponse, PermissionConfig, StatsToday } from '@/types/api'

function getToken(): string {
  try {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('token')
    if (token) {
      localStorage.setItem('fxbot_console_token', token)
      return token
    }
  } catch {}
  return localStorage.getItem('fxbot_console_token') || ''
}

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
export const configApi = {
  getAll: () => api.get<Record<string, any>>('/config'),
  getTabs: () => api.get<any[]>('/config/tabs'),
  save: (configs: Record<string, any>) => api.put<ApiResponse>('/config', configs),
}

// 权限 API
export const permissionApi = {
  get: () => api.get<PermissionConfig>('/permissions'),
  save: (config: PermissionConfig) => api.put<ApiResponse>('/permissions', config),
}

// 统计 API
export const statsApi = {
  getToday: () => api.get<StatsToday>('/stats/today'),
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
  leave: (groupId: number) =>
    api.post<{ left: number }>('/membership/leave', { group_id: groupId }),
  notify: (groupIds: number[], text: string, images?: string[]) =>
    api.post<{ sent: number }>('/membership/notify', { group_ids: groupIds, text, images }),
  runJob: () => api.post<{ reminded: number; left: number }>('/membership/job/run'),
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
}
