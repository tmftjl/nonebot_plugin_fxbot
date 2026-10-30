// -*- coding: utf-8 -*-
import { chatApi } from '@/api'
import type { ChatTenant } from '@/types/api'

let cached: Promise<ChatTenant[]> | null = null

/** 已有会话列表；同一页面内多个选择器共用一次请求。 */
export const loadTenants = (): Promise<ChatTenant[]> => {
  cached ||= chatApi
    .tenants()
    .then((data) => data.tenants || [])
    .catch(() => {
      cached = null
      return []
    })
  return cached
}
