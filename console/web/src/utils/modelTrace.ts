import type { ChatModelTrace } from '@/types/api'

export type TraceRecord = Record<string, unknown>

export function traceRecords(value: unknown): TraceRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is TraceRecord => !!item && typeof item === 'object' && !Array.isArray(item))
    : []
}

export function traceStage(task: unknown): string {
  const key = String(task || '')
  return ({ reply: '回复器', replyer: '回复器', planner: '规划器', utils: '辅助任务',
    fast_model: '快速模型', vision: '视觉理解', memory: '记忆整理', embedding: '向量嵌入',
    learner: '学习器' } as Record<string, string>)[key] || key || '模型调用'
}

export function traceDuration(value: unknown): string {
  const ms = Number(value || 0)
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`
}

export function traceAttemptLabel(attempt: TraceRecord, index: number): string {
  return ({ 'replyer.reasoning_initial': '首次推理', 'replyer.reasoning_continuation': '接续推理',
    'replyer.reasoning_regeneration': '重新生成' } as Record<string, string>)[String(attempt.workflow_purpose || '')]
    || `第 ${index + 1} 次调用`
}

export function traceItemKind(item: TraceRecord): string {
  const type = String(item.type || '')
  if (type === 'reasoning' || type === 'error') return type
  if (type === 'function_call' || type === 'function_call_output') return type
  return String(item.role || 'assistant')
}

export function traceItemLabel(item: TraceRecord): string {
  const kind = traceItemKind(item)
  return ({ reasoning: '思考过程', system: '系统指令', developer: '开发者指令', user: '用户消息',
    assistant: '模型回复', tool: '工具结果', function_call: '工具调用',
    function_call_output: '工具结果', error: '调用异常' } as Record<string, string>)[kind] || kind
}

export function traceText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(traceText).filter(Boolean).join('\n\n')
  if (!value || typeof value !== 'object') return ''
  const item = value as TraceRecord
  for (const key of ['text', 'refusal', 'content', 'summary']) {
    const text = traceText(item[key])
    if (text.trim()) return text
  }
  return ''
}

export function traceItemText(item: TraceRecord): string {
  if (item.type === 'error') return String(item.error || '本次调用失败')
  if (item.type === 'function_call_output' || item.role === 'tool') {
    return traceText(item.output ?? item.content) || traceReadable(item.output ?? item.content)
  }
  return traceText(item)
}

/** 把结构化数据压成「键: 值」文本，不暴露 JSON 语法。 */
export function traceReadable(value: unknown, depth = 0): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value)) {
    return value.map((item) => traceReadable(item, depth + 1)).filter(Boolean).join(depth ? '、' : '\n')
  }
  const lines = Object.entries(value as TraceRecord)
    .map(([key, item]) => {
      const text = traceReadable(item, depth + 1)
      return text ? `${key}: ${text}` : ''
    })
    .filter(Boolean)
  return lines.join(depth ? '；' : '\n')
}

/** 工具参数拆成逐项字段，供定义列表展示。 */
export function traceFields(value: unknown): Array<{ key: string; value: string }> {
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return []
    try {
      return traceFields(JSON.parse(text))
    } catch {
      return [{ key: '', value }]
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    const text = traceReadable(value)
    return text ? [{ key: '', value: text }] : []
  }
  return Object.entries(value as TraceRecord)
    .map(([key, item]) => ({ key, value: traceReadable(item, 1) || '—' }))
}

export function traceReasoning(snapshot: TraceRecord): TraceRecord[] {
  const attempts = traceRecords(snapshot.generation_attempts)
  const reasoning = attempts.flatMap((attempt, index) => {
    const normalized = traceRecords(attempt.output_items).filter((item) => item.type === 'reasoning')
    const items = normalized.length ? normalized
      : traceRecords(attempt.provider_output_items).filter((item) => item.type === 'reasoning')
    return items.map((item): TraceRecord => ({ ...item, attemptLabel: traceAttemptLabel(attempt, index) }))
  })
  return reasoning.length ? reasoning : traceRecords(snapshot.output_items).filter((item) => item.type === 'reasoning')
}

export function filterModelTraces(items: ChatModelTrace[], query: string, stage: string, status: string): ChatModelTrace[] {
  const search = query.trim().toLocaleLowerCase()
  return items.filter((item) => (!stage || (item.task || item.category) === stage)
    && (!status || item.status === status)
    && (!search || [item.model_name, item.provider, item.turn_id, item.reply_preview, item.reasoning_preview,
      traceStage(item.task || item.category)].join(' ').toLocaleLowerCase().includes(search)))
}
