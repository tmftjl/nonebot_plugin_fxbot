// -*- coding: utf-8 -*-
import { api } from './index'
import type { LocalChatEvent, LocalChatState } from '@/types/api'

export const localChatApi = {
  options: () => api.get<{ personas: string[]; providers: string[] }>('/local-chat/options'),
  history: (id: string) => api.get<LocalChatState>(`/local-chat/${encodeURIComponent(id)}`),
  clear: (id: string) => api.delete<{ history_cleared: boolean }>(`/local-chat/${encodeURIComponent(id)}`),
  stop: (id: string, messageId: string) =>
    api.post(`/local-chat/${encodeURIComponent(id)}/stop`, { message_id: messageId }),
  send: (
    id: string,
    data: { text: string; message_id: string; nickname: string; persona: string; provider: string },
    onEvent: (event: LocalChatEvent) => void,
    signal: AbortSignal,
  ) => api.stream(`/local-chat/${encodeURIComponent(id)}/messages`, data, onEvent, signal),
}
