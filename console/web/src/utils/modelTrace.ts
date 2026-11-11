import type { ChatModelTrace } from '@/types/api'

export type TraceRecord = Record<string, unknown>

export function traceRecords(value: unknown): TraceRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is TraceRecord => !!item && typeof item === 'object' && !Array.isArray(item))
    : []
}

export interface TraceGroup {
  key: string
  turnId: string
  paths: string[]
  items: ChatModelTrace[]
  title: string
  models: string[]
  createdAt: string
  duration: number
  status: string
  count: number
}

export interface TraceStep {
  index: number
  metadata: TraceRecord
  model: string
  provider: string
  duration: number
  tokens: number | null
  status: string
  error: string
  attempts: TraceRecord[]
  requestItems: TraceRecord[]
  outputItems: TraceRecord[]
  toolDefinitions: TraceRecord[]
}

const asRecord = (value: unknown): TraceRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as TraceRecord : {}

const byCreated = (left: ChatModelTrace, right: ChatModelTrace): number =>
  (left.created_at || '').localeCompare(right.created_at || '') || left.path.localeCompare(right.path)

/** 同一次触发产生的模型调用按轮次合并，避免按规划器/回复器拆成多条记录。 */
export function groupModelTraces(items: ChatModelTrace[]): TraceGroup[] {
  const buckets = new Map<string, ChatModelTrace[]>()
  for (const item of items) {
    const key = item.turn_id ? `turn:${item.turn_id}` : `path:${item.path}`
    const bucket = buckets.get(key)
    if (bucket) bucket.push(item)
    else buckets.set(key, [item])
  }
  return [...buckets.entries()].map(([key, members]) => {
    members.sort(byCreated)
    const first = members[0]
    const last = members[members.length - 1]
    const preview = (field: 'reply_preview' | 'reasoning_preview') =>
      [...members].reverse().find((member) => member[field])?.[field] || ''
    return {
      key,
      turnId: first.turn_id,
      paths: members.map((member) => member.path),
      items: members,
      title: preview('reply_preview') || preview('reasoning_preview') || '本次处理没有可见文字',
      models: [...new Set(members.map((member) => member.model_name || member.provider).filter(Boolean))],
      createdAt: first.created_at,
      duration: members.reduce((total, member) => total + (Number(member.duration_ms) || 0), 0),
      status: members.some((member) => member.status === 'failed') ? 'failed' : last.status,
      count: members.length,
    }
  }).sort((left, right) => (right.createdAt || '').localeCompare(left.createdAt || '') || right.key.localeCompare(left.key))
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

export function filterTraceGroups(groups: TraceGroup[], query: string, status: string): TraceGroup[] {
  const search = query.trim().toLocaleLowerCase()
  return groups.filter((group) => (!status || group.status === status)
    && (!search || [group.title, group.turnId, ...group.models,
      ...group.items.flatMap((item) => [item.reply_preview, item.reasoning_preview])]
      .join(' ').toLocaleLowerCase().includes(search)))
}

/** 把一次处理里的多份快照按时间展开成调用步骤。 */
export function traceSteps(snapshots: TraceRecord[]): TraceStep[] {
  return snapshots.map((snapshot, index) => {
    const metadata = asRecord(snapshot.metadata)
    const attempts = traceRecords(snapshot.generation_attempts)
    const last = attempts[attempts.length - 1] || {}
    const totalTokens = metadata.total_tokens
    return {
      index,
      metadata,
      model: String(metadata.model_name || ''),
      provider: String(metadata.provider || ''),
      duration: Number(metadata.duration_ms) || 0,
      tokens: typeof totalTokens === 'number' ? totalTokens : null,
      status: String(last.status || ''),
      error: String(metadata.error || last.error || ''),
      attempts,
      requestItems: traceRecords(snapshot.request_items),
      outputItems: traceRecords(attempts.length ? last.output_items : snapshot.output_items),
      toolDefinitions: traceRecords(snapshot.tool_definitions),
    }
  })
}

const stepOutputItems = (step: TraceStep): TraceRecord[] =>
  step.attempts.length ? step.attempts.flatMap((attempt) => traceRecords(attempt.output_items)) : step.outputItems

/** 合并所有步骤的输出；工具结果插在产生它的下一次请求之前，保持真实发生顺序。 */
export function traceProcessing(steps: TraceStep[]): { timeline: TraceRecord[]; replies: TraceRecord[] } {
  const calls = new Set<string>()
  const results = new Set<string>()
  const timeline: TraceRecord[] = []
  const replies: TraceRecord[] = []
  for (const step of steps) {
    for (const message of step.requestItems) {
      if (message.role !== 'tool' && message.type !== 'function_call_output') continue
      const id = String(message.tool_call_id || message.call_id || '')
      if (!id || results.has(id) || !calls.has(id)) continue
      results.add(id)
      timeline.push({
        type: 'function_call_output',
        role: 'tool',
        name: String(message.name || ''),
        output: message.content ?? message.output ?? message.text,
      })
    }
    for (const item of stepOutputItems(step)) {
      if (item.type === 'message') {
        replies.push(item)
        continue
      }
      timeline.push(item)
      const id = String(item.call_id || '')
      if (item.type === 'function_call' && id) calls.add(id)
    }
  }
  return { timeline, replies }
}
