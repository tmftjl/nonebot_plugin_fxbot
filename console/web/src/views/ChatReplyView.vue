<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatTenantPicker from '@/components/ChatTenantPicker.vue'
import { chatApi } from '@/api'
import type {
  ChatTenant,
  ChatSessionDigest,
  ChatSessionSettings,
  ChatRecentMessage,
  ChatPlannerSample,
  ChatReplyGate,
  ChatReplyGateObservation,
  ChatHealth,
  ChatTrace,
  ChatTraceStage,
  ChatModelTrace,
} from '@/types/api'

const TAB_KEY = 'fxbot-chat-reply-tab-v2'

const tabs = [
  { key: 'overview', label: '会话详情', hint: '身份、配置与最近思考' },
  { key: 'planner', label: '规划样本', hint: 'Planner 首轮动作' },
  { key: 'gate', label: '主动接话门控', hint: 'reply 概率与窗口' },
  { key: 'trace', label: '轮次追踪', hint: '每轮注入与耗时' },
  { key: 'snapshot', label: '模型快照', hint: '完整 prompt 与输出' },
] as const

type TabKey = (typeof tabs)[number]['key']
const savedTab = localStorage.getItem(TAB_KEY)
const activeTab = ref<TabKey>(
  tabs.some((tab) => tab.key === savedTab) ? (savedTab as TabKey) : 'overview',
)

watch(activeTab, (value) => localStorage.setItem(TAB_KEY, value))

const emptyReplyGate = (): ChatReplyGate => ({
  calibrated: false,
  window: {
    window_seconds: 3600,
    scored_rounds: 0,
    expected_proactive: 0,
    expected_forced: 0,
    expected_replies: 0,
    actual_replies: 0,
  },
  backoff: { pending: 0, delay_seconds: 0 },
  observations: [],
  distribution: [],
  proactive: {
    talk_value: 0,
    switch: null,
  },
})

const tenants = ref<ChatTenant[]>([])
const tenant = ref('')
const health = ref<ChatHealth | null>(null)
const healthLoading = ref(false)
const plannerSamples = ref<ChatPlannerSample[]>([])
const plannerLoading = ref(false)
const replyGate = ref<ChatReplyGate>(emptyReplyGate())
const gateLoading = ref(false)
const traces = ref<ChatTrace[]>([])
const traceLoading = ref(false)
const modelTraces = ref<ChatModelTrace[]>([])
const modelTraceLoading = ref(false)
const snapshotDialog = ref(false)
const snapshotLoading = ref(false)
const snapshotDetail = ref<Record<string, any> | null>(null)
const loaded = ref(false)
const emptySessionForm = () => ({ persona: '', provider: '', talkOverride: false, talkValue: 100 })
const sessionSettings = ref<ChatSessionSettings | null>(null)
const sessionLoading = ref(false)
const sessionSaving = ref(false)
const sessionForm = ref(emptySessionForm())
const sessionDigest = ref<ChatSessionDigest | null>(null)
const digestLoading = ref(false)
const recentMessages = ref<ChatRecentMessage[]>([])
const messagesLoading = ref(false)
let sessionRequest = 0

const RECORD_PAGE_SIZE = 20
const plannerPage = ref(1)
const tracePage = ref(1)
const snapshotPage = ref(1)
const plannerRows = computed(() =>
  plannerSamples.value.slice((plannerPage.value - 1) * RECORD_PAGE_SIZE, plannerPage.value * RECORD_PAGE_SIZE),
)
const traceRows = computed(() =>
  traces.value.slice((tracePage.value - 1) * RECORD_PAGE_SIZE, tracePage.value * RECORD_PAGE_SIZE),
)
const snapshotRows = computed(() =>
  modelTraces.value.slice((snapshotPage.value - 1) * RECORD_PAGE_SIZE, snapshotPage.value * RECORD_PAGE_SIZE),
)

const currentTenant = computed(() => tenants.value.find((item) => item.tenant === tenant.value) || null)
const tenantParts = computed(() => {
  const parts = (tenant.value || '').split(':')
  const scene = parts[2]
  return {
    platform: parts[0] || '—',
    bot: parts[1] || '',
    scene: scene === 'group' ? '群聊' : scene === 'private' ? '私聊' : '会话',
    target: parts.slice(3).join(':') || tenant.value || '—',
  }
})
const latestTrace = computed(() => traces.value[0] || null)
const latestSnapshot = computed(() => modelTraces.value[0] || null)
const latestStages = computed(() => (latestTrace.value ? traceStages(latestTrace.value) : []))

const percentText = (value: number | null | undefined) => `${Math.round(Number(value ?? 0) * 100)}%`
const sessionSwitchText = computed(() => {
  const value = sessionSettings.value?.proactive.switch
  return value === null || value === undefined ? '未设置' : value ? '已开启' : '已关闭'
})
const hasSessionOverrides = computed(() => {
  const settings = sessionSettings.value
  return !!settings && (!!settings.persona.bound || !!settings.provider.bound || settings.talk_value.bound !== null)
})

const digestState = computed(() => sessionDigest.value?.state || null)
const turnCounts = computed(() => {
  const counts = sessionDigest.value?.turns || {}
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0)
  const issues =
    Number(counts.failed || 0) + Number(counts.partial || 0) + Number(counts.aborted || 0)
  return { total, issues, sent: Number(counts.sent || 0) }
})
const jargonCount = computed(() => {
  const counts = sessionDigest.value?.jargon || {}
  return Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0)
})

const plannerLabelCounts = computed(() => {
  const replies = plannerSamples.value.filter((row) => row.label === 1).length
  return { replies, silent: plannerSamples.value.length - replies }
})

const proactiveState = computed(() => {
  const cfg = replyGate.value.proactive
  return {
    effective: cfg.switch === true,
    switchText: cfg.switch === null ? '未设置' : cfg.switch ? '开启' : '关闭',
  }
})

const formatTime = (value: string | null | undefined) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const formatCount = (value: number) => Number(value || 0).toFixed(3)

const loadTenants = async () => {
  try {
    const result = await chatApi.tenants()
    tenants.value = result.tenants || []
    // 清空历史后列表可能暂时不含该会话，仍保留用户当前选择。
    if (!tenant.value && tenants.value.length) tenant.value = tenants.value[0].tenant
  } catch (error) {
    console.error('加载聊天租户失败', error)
    ElMessage.error('加载聊天租户失败，请重试')
  }
}

const applySessionSettings = (settings: ChatSessionSettings) => {
  sessionSettings.value = settings
  sessionForm.value = {
    persona: settings.persona.bound,
    provider: settings.provider.bound,
    talkOverride: settings.talk_value.bound !== null,
    talkValue: Math.round((settings.talk_value.bound ?? settings.talk_value.effective) * 100),
  }
}

const loadSessionSettings = async () => {
  const request = ++sessionRequest
  sessionSettings.value = null
  sessionForm.value = emptySessionForm()
  if (!tenant.value) {
    sessionSettings.value = null
    sessionForm.value = emptySessionForm()
    sessionLoading.value = false
    return
  }
  sessionLoading.value = true
  try {
    const settings = await chatApi.getSession(tenant.value)
    if (request === sessionRequest) applySessionSettings(settings)
  } catch (error) {
    console.error('加载会话设置失败', error)
    if (request === sessionRequest) {
      sessionSettings.value = null
      sessionForm.value = emptySessionForm()
      ElMessage.error('加载会话设置失败，请重试')
    }
  } finally {
    if (request === sessionRequest) sessionLoading.value = false
  }
}

const loadSessionDigest = async () => {
  const target = tenant.value
  if (!target) {
    sessionDigest.value = null
    digestLoading.value = false
    return
  }
  digestLoading.value = true
  try {
    const result = await chatApi.getSessionDigest(target)
    if (tenant.value === target) sessionDigest.value = result.digest
  } catch (error) {
    console.error('加载会话摘要失败', error)
    if (tenant.value === target) sessionDigest.value = null
  } finally {
    if (tenant.value === target) digestLoading.value = false
  }
}

const loadRecentMessages = async () => {
  const target = tenant.value
  if (!target) {
    recentMessages.value = []
    messagesLoading.value = false
    return
  }
  messagesLoading.value = true
  try {
    const result = await chatApi.listMessages(target)
    if (tenant.value === target) recentMessages.value = result.items || []
  } catch (error) {
    console.error('加载最近对话失败', error)
    if (tenant.value === target) recentMessages.value = []
    ElMessage.error('加载最近对话失败，请重试')
  } finally {
    if (tenant.value === target) messagesLoading.value = false
  }
}

const saveSessionSettings = async () => {
  if (!tenant.value || sessionLoading.value || sessionSaving.value || !sessionSettings.value) return
  const target = tenant.value
  const request = ++sessionRequest
  sessionSaving.value = true
  try {
    const result = await chatApi.saveSession({
      tenant: target,
      persona: sessionForm.value.persona || null,
      provider: sessionForm.value.provider || null,
      talk_value: sessionForm.value.talkOverride ? sessionForm.value.talkValue / 100 : null,
    })
    if (target !== tenant.value || request !== sessionRequest) return
    applySessionSettings(result.settings)
    ElMessage.success(result.history_cleared ? '已保存，并清空该会话短期历史' : '会话设置已保存')
    if (result.history_cleared) {
      await loadTenants()
      if (activeTab.value === 'overview') void refreshOverview()
    }
    if (activeTab.value === 'gate') void loadReplyGate()
  } catch (error) {
    console.error('保存会话设置失败', error)
    ElMessage.error('保存会话设置失败，请重试')
  } finally {
    sessionSaving.value = false
  }
}

const resetSessionSettings = async () => {
  if (!tenant.value || sessionLoading.value || sessionSaving.value) return
  const target = tenant.value
  const request = sessionRequest
  try {
    await ElMessageBox.confirm(
      '将人格、Provider 与回复频率恢复为全局默认。若本会话指定过人格，会同时清空短期历史。',
      '恢复全局设置',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (target !== tenant.value || request !== sessionRequest || sessionSaving.value) return
  sessionSaving.value = true
  try {
    const result = await chatApi.saveSession({
      tenant: target,
      persona: null,
      provider: null,
      talk_value: null,
    })
    if (target !== tenant.value || request !== sessionRequest) return
    applySessionSettings(result.settings)
    ElMessage.success(result.history_cleared ? '已恢复全局设置，并清空该会话短期历史' : '已恢复全局设置')
    if (result.history_cleared) {
      await loadTenants()
      if (activeTab.value === 'overview') void refreshOverview()
    }
    if (activeTab.value === 'gate') void loadReplyGate()
  } catch (error) {
    console.error('恢复会话设置失败', error)
    ElMessage.error('恢复会话设置失败，请重试')
  } finally {
    sessionSaving.value = false
  }
}

const loadHealth = async () => {
  healthLoading.value = true
  try {
    health.value = await chatApi.getHealth()
  } catch (error) {
    console.error('加载聊天状态失败', error)
    health.value = null
  } finally {
    healthLoading.value = false
  }
}

const loadPlannerSamples = async () => {
  const target = tenant.value
  if (!tenant.value) {
    plannerSamples.value = []
    return
  }
  plannerLoading.value = true
  try {
    const result = await chatApi.listPlannerSamples(target)
    if (target !== tenant.value) return
    plannerSamples.value = result.items || []
    plannerPage.value = 1
  } catch (error) {
    console.error('加载规划样本失败', error)
    ElMessage.error('加载规划样本失败，请重试')
  } finally {
    plannerLoading.value = false
  }
}

const plannerFeature = (row: ChatPlannerSample, key: string) => {
  const value = row.features?.[key]
  return value === undefined || value === null ? '—' : String(value)
}

const loadReplyGate = async () => {
  const target = tenant.value
  if (!tenant.value) {
    replyGate.value = emptyReplyGate()
    return
  }
  gateLoading.value = true
  try {
    const result = await chatApi.getReplyGate(target)
    if (target === tenant.value) replyGate.value = result
  } catch (error) {
    console.error('加载门控观察失败', error)
    ElMessage.error('加载门控观察失败，请重试')
  } finally {
    gateLoading.value = false
  }
}

const gateFeature = (row: ChatReplyGateObservation, key: string) => {
  const value = row.features?.[key]
  return value === undefined || value === null ? '—' : String(value)
}

const loadTraces = async () => {
  const target = tenant.value
  if (!tenant.value) {
    traces.value = []
    return
  }
  traceLoading.value = true
  try {
    const result = await chatApi.listTraces(target)
    if (target !== tenant.value) return
    traces.value = result.items || []
    tracePage.value = 1
  } catch (error) {
    console.error('加载轮次追踪失败', error)
    ElMessage.error('加载轮次追踪失败，请重试')
  } finally {
    traceLoading.value = false
  }
}

const traceInjected = (row: Pick<ChatTrace, 'injected'>) => {
  const injected = row.injected || {}
  const parts: string[] = []
  if (injected.style) parts.push('风格')
  if (Array.isArray(injected.jargon) && injected.jargon.length) parts.push(`黑话 ${injected.jargon.length}`)
  if (Array.isArray(injected.expressions) && injected.expressions.length) {
    parts.push(`表达 ${injected.expressions.length}`)
  }
  if (injected.chars?.total) parts.push(`共 ${injected.chars.total} 字`)
  return parts.join(' · ') || '无注入'
}

const traceBudget = (row: Pick<ChatTrace, 'budget'>) => {
  const budget = row.budget || {}
  const parts: string[] = []
  if (budget.max_context_size !== undefined) {
    parts.push(`历史 ${budget.max_context_size} 条`)
  }
  if (budget.memory_chars !== undefined) parts.push(`长期记忆与学习 ${budget.memory_chars} 字`)
  if (budget.tools !== undefined) parts.push(`工具 ${budget.tools}`)
  if (budget.plan_action) parts.push(`规划 ${budget.plan_action}`)
  return parts.join(' · ') || '—'
}

const traceSend = (row: Pick<ChatTrace, 'budget'>) => {
  const send = row.budget?.send
  if (!send) return '未发送'
  const status =
    send.failed_index !== null && send.failed_index !== undefined
      ? `第 ${send.failed_index + 1} 段失败`
      : send.cancelled
        ? '发送前取消'
        : send.sent_parts < send.total_parts
          ? '部分送达'
          : '已送达'
  return `${status} · ${send.sent_parts}/${send.total_parts} 段 · ${send.send_ms ?? 0} ms`
}

const traceStages = (row: ChatTrace): ChatTraceStage[] =>
  Array.isArray(row.budget?.stages) ? row.budget.stages : []

const traceTiming = (row: ChatTrace) => {
  const timings = row.budget?.timings
  if (!timings) return ''
  return `Planner ${timings.planner_ms} ms · 回复生成 ${timings.replyer_ms} ms · 发送 ${timings.send_ms} ms · 其他处理 ${timings.other_ms} ms`
}

const loadModelTraces = async () => {
  const target = tenant.value
  if (!tenant.value) {
    modelTraces.value = []
    return
  }
  modelTraceLoading.value = true
  try {
    const result = await chatApi.listModelTraces(target)
    if (target !== tenant.value) return
    modelTraces.value = result.items || []
    snapshotPage.value = 1
  } catch (error) {
    console.error('加载模型快照失败', error)
    ElMessage.error('加载模型快照失败，请重试')
  } finally {
    modelTraceLoading.value = false
  }
}

const openSnapshot = async (row: ChatModelTrace) => {
  snapshotDialog.value = true
  snapshotLoading.value = true
  snapshotDetail.value = null
  snapshotOpen.value = ['request', 'output', 'attempts']
  try {
    const result = await chatApi.getModelTrace(row.path)
    snapshotDetail.value = result.snapshot
    const outputs = snapshotReasoning.value
    const hasReasoning = outputs.some((item: SnapshotRecord) => item?.type === 'reasoning' && item.text)
    snapshotOpen.value = hasReasoning
      ? ['reasoning', 'request', 'output', 'attempts']
      : ['request', 'output', 'attempts']
  } catch (error) {
    console.error('读取模型快照失败', error)
    ElMessage.error('读取模型快照失败，请重试')
    snapshotDialog.value = false
  } finally {
    snapshotLoading.value = false
  }
}

const snapshotText = computed(() =>
  snapshotDetail.value ? JSON.stringify(snapshotDetail.value, null, 2) : '',
)
const snapshotOpen = ref<string[]>(['request', 'output', 'attempts'])
type SnapshotRecord = Record<string, any>
const snapshotRowsOf = (name: string): SnapshotRecord[] => {
  const value = snapshotDetail.value?.[name]
  return Array.isArray(value) ? value : []
}
const snapshotMeta = computed(() => {
  const detail = snapshotDetail.value
  if (!detail) return []
  const meta = (detail.metadata || {}) as SnapshotRecord
  const attempts = snapshotRowsOf('generation_attempts')
  const last = (attempts[attempts.length - 1] || {}) as SnapshotRecord
  const tokens = [meta.prompt_tokens, meta.completion_tokens, meta.total_tokens]
  return [
    { label: '提供方', value: meta.provider || '—' },
    { label: '模型', value: meta.model_name || '—' },
    { label: '耗时', value: `${meta.duration_ms ?? 0} ms` },
    { label: 'Token', value: tokens.every((value) => value === undefined) ? '—' : tokens.map((v) => v ?? 0).join(' / ') },
    { label: '会话', value: meta.session_id || '—' },
    { label: '轮次', value: meta.turn_id || '—' },
    { label: '类型', value: meta.task || detail.request?.kind || '—' },
    { label: '结果', value: last.status === 'failed' ? '失败' : last.status ? '完成' : '—' },
  ]
})
const snapshotAttempts = computed(() => snapshotRowsOf('generation_attempts'))
const snapshotRequests = computed(() => snapshotRowsOf('request_items'))
const snapshotOutputs = computed(() => snapshotRowsOf('output_items'))
const snapshotReasoning = computed(() =>
  snapshotAttempts.value.flatMap((attempt) => Array.isArray(attempt.output_items) ? attempt.output_items : [])
    .filter((item: SnapshotRecord) => item.type === 'reasoning' && String(item.text || '').trim()),
)
const snapshotTools = computed(() => snapshotRowsOf('tool_definitions'))
const snapshotItemText = (item: SnapshotRecord) => {
  if (typeof item.text === 'string') return item.text
  if (item.type === 'function_call') return `${item.name || 'tool'}(${item.arguments || ''})`
  if (typeof item.content === 'string') return item.content
  return JSON.stringify(item, null, 2)
}

const formatBytes = (value: number) => {
  const size = Number(value || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const refreshOverview = () =>
  Promise.all([loadTraces(), loadModelTraces(), loadRecentMessages(), loadSessionDigest()])

const loadCurrent = async () => {
  if (activeTab.value === 'overview') await refreshOverview()
  else if (activeTab.value === 'planner') await loadPlannerSamples()
  else if (activeTab.value === 'gate') await loadReplyGate()
  else if (activeTab.value === 'snapshot') await loadModelTraces()
  else await loadTraces()
}

const refresh = async () => {
  await loadTenants()
  await Promise.all([loadHealth(), loadSessionSettings(), loadCurrent()])
  loaded.value = true
}

const onTenantChange = async () => {
  sessionDigest.value = null
  recentMessages.value = []
  modelTraces.value = []
  traces.value = []
  plannerSamples.value = []
  replyGate.value = emptyReplyGate()
  await Promise.all([loadSessionSettings(), loadCurrent()])
}

watch(activeTab, () => {
  void loadCurrent()
})

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="chat-reply-view">
    <PageHeader title="接话与运行" subtitle="按会话查看身份、配置与最近思考，并调整人格、Provider 与回复频率">
      <template #actions>
        <el-button :icon="Refresh" :loading="healthLoading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <div v-loading="sessionLoading" class="panel session-panel">
      <div class="session-head">
        <div class="session-picker">
          <span class="session-eyebrow">当前会话</span>
          <ChatTenantPicker v-model="tenant" :tenants="tenants" @change="onTenantChange" />
        </div>
        <div v-if="sessionSettings" class="session-tags">
          <el-tag effect="plain" size="small">人格 · {{ sessionSettings.persona.effective }}</el-tag>
          <el-tag effect="plain" size="small" type="info">
            Provider · {{ sessionSettings.provider.effective || '未指定' }}
          </el-tag>
          <el-tag effect="plain" size="small" type="success">
            频率 · {{ percentText(sessionSettings.talk_value.effective) }}
          </el-tag>
        </div>
      </div>

      <div v-if="tenant" class="session-facts">
        <span>{{ tenantParts.scene }}</span>
        <span>{{ tenantParts.platform }}</span>
        <span v-if="tenantParts.bot">Bot {{ tenantParts.bot }}</span>
        <span>{{ currentTenant?.messages ?? 0 }} 条消息</span>
        <span>学习 {{ currentTenant?.learn_count ?? 0 }} · 使用 {{ currentTenant?.use_count ?? 0 }}</span>
        <span>主动接话 {{ sessionSwitchText }}</span>
        <code class="tenant-id">{{ tenant }}</code>
      </div>
      <div v-else class="tenant-help">选择会话后可查看详情并设置本会话的人格、Provider 与回复频率。</div>

      <div v-if="tenant" class="session-settings">
        <div class="setting-field">
          <div class="setting-label-row">
            <label>人格</label>
            <el-tag v-if="sessionForm.persona" size="small" type="warning">仅本会话</el-tag>
          </div>
          <el-select v-model="sessionForm.persona" placeholder="跟随全局">
            <el-option label="跟随全局" value="" />
            <el-option
              v-for="name in sessionSettings?.persona.options || []"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
          <span class="setting-hint">
            {{
              sessionForm.persona
                ? '切换人格会清空本会话短期历史'
                : `当前生效：${sessionSettings?.persona.effective || '—'}`
            }}
          </span>
        </div>

        <div class="setting-field">
          <div class="setting-label-row">
            <label>Provider</label>
            <el-tag v-if="sessionForm.provider" size="small" type="warning">仅本会话</el-tag>
          </div>
          <el-select v-model="sessionForm.provider" placeholder="跟随路由与默认">
            <el-option label="跟随路由与默认" value="" />
            <el-option
              v-for="name in sessionSettings?.provider.options || []"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
          <span class="setting-hint">
            {{
              sessionForm.provider
                ? '本会话固定使用该 Provider'
                : `当前生效：${sessionSettings?.provider.effective || '未指定'}`
            }}
          </span>
        </div>

        <div class="setting-field setting-frequency">
          <div class="setting-label-row">
            <label>回复频率</label>
            <el-switch
              v-model="sessionForm.talkOverride"
              size="small"
              inline-prompt
              active-text="覆盖"
              inactive-text="全局"
            />
          </div>
          <el-slider
            v-model="sessionForm.talkValue"
            :min="0"
            :max="100"
            :step="1"
            :disabled="!sessionForm.talkOverride"
          />
          <span class="setting-hint">
            {{
              sessionForm.talkOverride
                ? `本会话覆盖为 ${sessionForm.talkValue}%`
                : `当前生效：${percentText(sessionSettings?.talk_value.effective)}`
            }}
          </span>
        </div>

        <div class="setting-actions">
          <el-button type="primary" :loading="sessionSaving" :disabled="sessionLoading || !sessionSettings" @click="saveSessionSettings">保存设置</el-button>
          <el-button :loading="sessionSaving" :disabled="sessionLoading || !hasSessionOverrides" @click="resetSessionSettings">
            恢复全局
          </el-button>
        </div>
      </div>

      <div class="tenant-help">
        Provider 与回复频率保存后立即生效；人格切换会清空本会话短期历史，学习与长期记忆不受影响。
      </div>
    </div>

    <div class="panel health-panel">
      <div class="panel-header">
        <h3 class="panel-title">运行状态</h3>
        <el-button :icon="Refresh" :loading="healthLoading" @click="loadHealth">刷新</el-button>
      </div>
      <div class="meta-grid">
        <span>
          对话
          <el-tag :type="health?.enabled ? 'success' : 'info'" size="small">
            {{ health?.enabled ? '已启用' : '未启用' }}
          </el-tag>
        </span>
        <span>
          聊天存储
          <el-tag :type="health?.storage_ready ? 'success' : 'danger'" size="small">
            {{ health?.storage_ready ? '就绪' : '未就绪' }}
          </el-tag>
        </span>
        <span>
          调度器
          <el-tag :type="health?.scheduler_ready ? 'success' : 'warning'" size="small">
            {{ health?.scheduler_ready ? '运行中' : '未加载' }}
          </el-tag>
        </span>
        <span>在线 Bot：{{ health?.online_bots ?? 0 }}</span>
        <span v-if="health?.degraded_reason">降级：{{ health.degraded_reason }}</span>
      </div>
    </div>

    <PillTabs v-if="loaded" v-model="activeTab" :items="tabs" class="reply-tabs" />

    <Transition name="tab-swap" mode="out-in">
      <div :key="activeTab" class="tab-stack">
        <div v-if="activeTab === 'overview'" class="panel overview-panel">
          <div class="panel-header">
            <h3 class="panel-title">会话详情</h3>
            <el-button :icon="Refresh" :loading="traceLoading || modelTraceLoading" @click="refreshOverview">
              刷新
            </el-button>
          </div>
          <div class="tenant-help">当前会话的身份、生效配置，以及最近一轮对话与最近一次模型调用。</div>

          <div class="overview-grid">
            <section class="overview-card">
              <h4 class="overview-title">会话身份</h4>
              <dl class="detail-list">
                <div>
                  <dt>场景</dt>
                  <dd>{{ tenantParts.scene }} · {{ tenantParts.platform }}</dd>
                </div>
                <div v-if="tenantParts.bot">
                  <dt>Bot</dt>
                  <dd>{{ tenantParts.bot }}</dd>
                </div>
                <div>
                  <dt>消息</dt>
                  <dd>{{ currentTenant?.messages ?? 0 }} 条</dd>
                </div>
                <div>
                  <dt>学习 / 使用</dt>
                  <dd>{{ currentTenant?.learn_count ?? 0 }} / {{ currentTenant?.use_count ?? 0 }}</dd>
                </div>
                <div>
                  <dt>最近活跃</dt>
                  <dd>{{ formatTime(digestState?.last_active_at) }}</dd>
                </div>
                <div>
                  <dt>轮次</dt>
                  <dd>
                    共 {{ turnCounts.total }} 轮 · 已发 {{ turnCounts.sent }}
                    <span v-if="turnCounts.issues" class="overview-error">· 异常 {{ turnCounts.issues }}</span>
                  </dd>
                </div>
                <div>
                  <dt>清空</dt>
                  <dd>
                    {{ digestState?.generation ? `${digestState.generation} 次` : '未清空' }}
                    <span v-if="digestState?.cleared_at">· 上次 {{ formatTime(digestState.cleared_at) }}</span>
                  </dd>
                </div>
                <div>
                  <dt>完整 ID</dt>
                  <dd><code class="tenant-id">{{ tenant || '未选择' }}</code></dd>
                </div>
              </dl>
            </section>

            <section class="overview-card">
              <h4 class="overview-title">本会话配置</h4>
              <dl class="detail-list">
                <div>
                  <dt>人格</dt>
                  <dd>
                    {{ sessionSettings?.persona.effective || '—' }}
                    <el-tag v-if="sessionSettings?.persona.bound" size="small" type="warning">会话</el-tag>
                  </dd>
                </div>
                <div>
                  <dt>Provider</dt>
                  <dd>
                    {{ sessionSettings?.provider.effective || '未指定' }}
                    <el-tag v-if="sessionSettings?.provider.bound" size="small" type="warning">会话</el-tag>
                  </dd>
                </div>
                <div>
                  <dt>回复频率</dt>
                  <dd>
                    {{ percentText(sessionSettings?.talk_value.effective) }}
                    <el-tag
                      v-if="sessionSettings && sessionSettings.talk_value.bound !== null"
                      size="small"
                      type="warning"
                    >
                      会话
                    </el-tag>
                  </dd>
                </div>
                <div>
                  <dt>主动接话</dt>
                  <dd>{{ sessionSwitchText }}</dd>
                </div>
              </dl>
            </section>

            <section class="overview-card overview-wide">
              <h4 class="overview-title">学习资产</h4>
              <dl class="detail-list overview-assets">
                <div>
                  <dt>群风格</dt>
                  <dd>
                    <template v-if="sessionDigest?.style">
                      v{{ sessionDigest.style.version }} · 规则 {{ sessionDigest.style.rules }} ·
                      示例 {{ sessionDigest.style.examples }} · 已用 {{ sessionDigest.style.use_count }} 次
                    </template>
                    <span v-else class="asset-empty">未学习</span>
                  </dd>
                </div>
                <div>
                  <dt>风格摘要</dt>
                  <dd class="asset-summary">{{ sessionDigest?.style?.summary || '—' }}</dd>
                </div>
                <div>
                  <dt>黑话</dt>
                  <dd>
                    共 {{ jargonCount }} 条 · 生效 {{ sessionDigest?.jargon.active ?? 0 }}
                    <template v-if="sessionDigest?.jargon.pending">
                      · 待审 {{ sessionDigest.jargon.pending }}
                    </template>
                  </dd>
                </div>
                <div>
                  <dt>表达</dt>
                  <dd>
                    共 {{ sessionDigest?.expressions.total ?? 0 }} 条 · 已确认
                    {{ sessionDigest?.expressions.checked ?? 0 }} · 已排除
                    {{ sessionDigest?.expressions.rejected ?? 0 }}
                  </dd>
                </div>
                <div>
                  <dt>表情包</dt>
                  <dd>
                    启用 {{ sessionDigest?.emoji.enabled ?? 0 }} / 共 {{ sessionDigest?.emoji.total ?? 0 }}
                  </dd>
                </div>
                <div v-for="job in ['expression', 'jargon']" :key="job">
                  <dt>{{ job === 'expression' ? '表达学习' : '黑话学习' }}</dt>
                  <dd v-if="sessionDigest?.cursors[job]">
                    游标后消息 {{ sessionDigest.cursors[job].backlog }} 条 · 失败 {{ sessionDigest.cursors[job].fail_count }}
                    <span v-if="sessionDigest.cursors[job].next_retry_at">
                      · 重试 {{ formatTime(sessionDigest.cursors[job].next_retry_at) }}
                    </span>
                    <span v-if="sessionDigest.cursors[job].last_error" class="overview-error">
                      · {{ sessionDigest.cursors[job].last_error }}
                    </span>
                  </dd>
                  <dd v-else class="asset-empty">尚未建立游标</dd>
                </div>
              </dl>
            </section>

            <section class="overview-card overview-wide">
              <div class="overview-title-row">
                <h4 class="overview-title">最近对话</h4>
                <el-button :icon="Refresh" link type="primary" :loading="messagesLoading" @click="loadRecentMessages">
                  刷新
                </el-button>
              </div>
              <div v-if="recentMessages.length" class="message-list">
                <div
                  v-for="(message, index) in recentMessages"
                  :key="index"
                  class="message-row"
                  :class="message.role"
                >
                  <div class="message-head">
                    <span class="message-role">
                      {{
                        message.role === 'assistant'
                          ? 'Bot'
                          : message.sender_name || message.sender_id || '用户'
                      }}
                    </span>
                    <span>{{ formatTime(message.timestamp) }}</span>
                    <el-tag v-if="message.at_bot" size="small" effect="plain">@</el-tag>
                  </div>
                  <p v-if="message.quote" class="message-quote">引用：{{ message.quote }}</p>
                  <p class="message-content">{{ message.content }}</p>
                </div>
              </div>
              <div v-else class="empty-hint">{{ messagesLoading ? '加载中…' : '暂无消息记录' }}</div>
            </section>

            <section class="overview-card overview-wide">
              <h4 class="overview-title">最近一轮</h4>
              <template v-if="latestTrace">
                <div class="meta-grid overview-meta">
                  <span>{{ formatTime(latestTrace.created_at) }}</span>
                  <span>触发：{{ latestTrace.trigger_kind || '—' }}</span>
                  <span>提供方：{{ latestTrace.provider || '—' }}</span>
                  <span>总耗时：{{ latestTrace.latency_ms }} ms</span>
                  <span>注入：{{ traceInjected(latestTrace) }}</span>
                  <span>发送：{{ traceSend(latestTrace) }}</span>
                </div>
                <p v-if="latestTrace.error" class="overview-error">错误：{{ latestTrace.error }}</p>
                <div v-if="latestStages.length" class="stage-list">
                  <div v-for="(stage, index) in latestStages" :key="index" class="stage-row">
                    <el-tag size="small" effect="plain">{{ stage.label }}</el-tag>
                    <span>{{ stage.provider || '—' }}</span>
                    <span>{{ stage.duration_ms }} ms</span>
                    <span v-if="stage.error" class="stage-error">{{ stage.error }}</span>
                  </div>
                </div>
                <div class="preview-block">
                  <span class="block-label">请求</span>
                  <p>{{ latestTrace.request_preview || '—' }}</p>
                </div>
                <div class="preview-block">
                  <span class="block-label">预算</span>
                  <p>{{ traceBudget(latestTrace) }}</p>
                </div>
              </template>
              <div v-else class="empty-hint">暂无轮次记录</div>
            </section>

            <section class="overview-card overview-wide">
              <div class="overview-title-row">
                <h4 class="overview-title">最近一次模型调用</h4>
                <el-button v-if="latestSnapshot" link type="primary" @click="openSnapshot(latestSnapshot)">
                  查看完整快照
                </el-button>
              </div>
              <template v-if="latestSnapshot">
                <div class="meta-grid overview-meta">
                  <span>{{ formatTime(latestSnapshot.created_at) }}</span>
                  <span>{{ latestSnapshot.output_title || latestSnapshot.model_name || '—' }}</span>
                  <span>轮次：{{ latestSnapshot.turn_id || '—' }}</span>
                  <span>耗时：{{ latestSnapshot.duration_ms }} ms</span>
                  <span>{{ latestSnapshot.status === 'failed' ? '失败' : '完成' }}</span>
                </div>
                <span class="block-label">思考过程</span>
                <pre class="thinking-text" :class="{ empty: !latestSnapshot.reasoning_preview }">{{
                  latestSnapshot.reasoning_preview || '本次调用没有记录思考内容'
                }}</pre>
                <span class="block-label">回复预览</span>
                <p class="preview-text">{{ latestSnapshot.reply_preview || '本次调用没有正文输出' }}</p>
              </template>
              <div v-else class="empty-hint">暂无模型快照</div>
            </section>
          </div>
        </div>

        <div v-if="activeTab === 'planner'" class="panel">
          <div class="panel-header">
            <h3 class="panel-title">规划样本</h3>
            <el-button :icon="Refresh" :loading="plannerLoading" @click="loadPlannerSamples">刷新</el-button>
          </div>
          <div class="tenant-help">
            Planner 每次调用记录首轮动作；label 为 1 表示决定回复，0 表示保持安静或等待。当前样本：回复
            {{ plannerLabelCounts.replies }} 条，不回复 {{ plannerLabelCounts.silent }} 条。
            规划样本保留 72 小时，全局最多 10,000 条。
          </div>
          <el-table :data="plannerRows" row-key="id" empty-text="暂无规划样本">
            <el-table-column label="时间" min-width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="trigger_kind" label="触发" width="100" />
            <el-table-column prop="action" label="动作" width="130" />
            <el-table-column label="标签" width="90">
              <template #default="{ row }">
                <el-tag :type="row.label ? 'success' : 'info'" size="small">
                  {{ row.label ? '回复' : '不回复' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="消息数" width="90">
              <template #default="{ row }">{{ plannerFeature(row, 'message_count') }}</template>
            </el-table-column>
            <el-table-column label="历史条数" width="100">
              <template #default="{ row }">{{ plannerFeature(row, 'history_len') }}</template>
            </el-table-column>
            <el-table-column label="问句" width="80">
              <template #default="{ row }">{{ plannerFeature(row, 'has_question') }}</template>
            </el-table-column>
            <el-table-column prop="model" label="模型" min-width="160" show-overflow-tooltip />
          </el-table>
          <el-pagination
            v-if="plannerSamples.length > RECORD_PAGE_SIZE"
            v-model:current-page="plannerPage"
            :page-size="RECORD_PAGE_SIZE"
            :total="plannerSamples.length"
            layout="prev, pager, next, total"
            class="table-pager"
            small
          />
        </div>

        <div v-if="activeTab === 'gate'" class="panel">
          <div class="panel-header">
            <h3 class="panel-title">主动接话门控</h3>
            <div class="panel-actions">
              <el-tag :type="replyGate.calibrated ? 'success' : 'warning'" size="small">
                {{ replyGate.calibrated ? '已校准' : '未校准' }}
              </el-tag>
              <el-tag :type="proactiveState.effective ? 'success' : 'info'" size="small">
                本群{{ proactiveState.effective ? '启用' : '未启用' }}
              </el-tag>
              <el-button :icon="Refresh" :loading="gateLoading" @click="loadReplyGate">刷新</el-button>
            </div>
          </div>
          <div class="tenant-help">
            普通群消息按发布先验计算 reply 概率；1 小时窗口累计预计回复与实际回复。状态只在内存，重启后清空。
          </div>
          <div class="meta-grid gate-window">
            <span>群级开关：{{ proactiveState.switchText }}</span>
            <span>talk_value：{{ replyGate.proactive.talk_value.toFixed(2) }}</span>
            <span>待处理：{{ replyGate.backoff.pending }}</span>
            <span>退避：{{ Math.round(replyGate.backoff.delay_seconds) }} 秒</span>
            <span>评分轮次 {{ replyGate.window.scored_rounds }}</span>
            <span>
              预计回复 {{ formatCount(replyGate.window.expected_replies) }}（主动
              {{ formatCount(replyGate.window.expected_proactive) }} / 强制
              {{ formatCount(replyGate.window.expected_forced) }}）
            </span>
            <span>窗口内实际回复 {{ replyGate.window.actual_replies }}</span>
            <span>窗口 {{ Math.round(replyGate.window.window_seconds / 60) }} 分钟</span>
          </div>
          <div class="gate-distribution">
            <span class="block-label">概率分布</span>
            <el-tag v-for="bucket in replyGate.distribution" :key="bucket.bucket" type="info" class="rule-tag">
              {{ bucket.bucket }}：{{ bucket.count }}
            </el-tag>
          </div>
          <el-table
            :data="replyGate.observations"
            empty-text="暂无门控观察；收到普通群消息后生成"
          >
            <el-table-column label="时间" min-width="170">
              <template #default="{ row }">{{ formatTime(row.occurred_at) }}</template>
            </el-table-column>
            <el-table-column label="概率" width="90">
              <template #default="{ row }">{{ formatCount(row.probability) }}</template>
            </el-table-column>
            <el-table-column label="阈值" width="90">
              <template #default="{ row }">{{ formatCount(row.threshold) }}</template>
            </el-table-column>
            <el-table-column label="判定" width="100">
              <template #default="{ row }">
                <el-tag :type="row.would_trigger ? 'success' : 'info'" size="small">
                  {{ row.would_trigger ? '放行' : '等待' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="预计/目标" width="130">
              <template #default="{ row }">
                {{ formatCount(row.expected_replies) }} / {{ formatCount(row.target_replies) }}
              </template>
            </el-table-column>
            <el-table-column label="保留比例" width="100">
              <template #default="{ row }">{{ formatCount(row.keep_ratio) }}</template>
            </el-table-column>
            <el-table-column label="待处理" width="80">
              <template #default="{ row }">{{ gateFeature(row, 'pending_count') }}</template>
            </el-table-column>
            <el-table-column label="近5分钟" width="90">
              <template #default="{ row }">{{ gateFeature(row, 'recent_message_count') }}</template>
            </el-table-column>
            <el-table-column label="自己占比" width="100">
              <template #default="{ row }">
                {{ formatCount(Number(gateFeature(row, 'recent_self_ratio'))) }}
              </template>
            </el-table-column>
            <el-table-column label="距上次发言" width="110">
              <template #default="{ row }">{{ gateFeature(row, 'seconds_since_bot_message') }}</template>
            </el-table-column>
            <el-table-column label="问句" width="80">
              <template #default="{ row }">{{ gateFeature(row, 'has_question_mark') }}</template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="activeTab === 'trace'" class="panel">
          <div class="panel-header">
            <h3 class="panel-title">轮次追踪</h3>
            <el-button :icon="Refresh" :loading="traceLoading" @click="loadTraces">刷新</el-button>
          </div>
          <div class="tenant-help">
            每轮对话一条记录，展开查看 Planner、回复生成与发送阶段；总耗时包含各阶段，不重复相加。
            轮次追踪保留 72 小时，全局最多 10,000 条。
          </div>
          <el-table :data="traceRows" row-key="id" empty-text="暂无轮次追踪">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="trace-detail">
                  <p v-if="traceTiming(row)" class="trace-timing">{{ traceTiming(row) }}</p>
                  <el-table v-if="traceStages(row).length" :data="traceStages(row)" size="small">
                    <el-table-column type="index" label="顺序" width="65" />
                    <el-table-column prop="label" label="阶段" width="130" />
                    <el-table-column prop="provider" label="提供方" min-width="160" />
                    <el-table-column label="耗时" width="100">
                      <template #default="{ row: stage }">{{ stage.duration_ms }} ms</template>
                    </el-table-column>
                    <el-table-column label="详情" min-width="260">
                      <template #default="{ row: stage }">
                        <template v-if="stage.kind === 'send'">{{ traceSend({ budget: { send: stage.send } }) }}</template>
                        <template v-else-if="stage.kind === 'replyer'">
                          {{ traceBudget({ budget: stage.budget || {} }) }} · {{ traceInjected({ injected: stage.injected || {} }) }}
                        </template>
                        <template v-else>行动决策</template>
                        <div v-if="stage.target_message_id">目标消息：{{ stage.target_message_id }}</div>
                      </template>
                    </el-table-column>
                    <el-table-column label="结果" min-width="160">
                      <template #default="{ row: stage }">{{ stage.error || '已完成' }}</template>
                    </el-table-column>
                  </el-table>
                  <span v-else class="form-hint">此记录没有阶段明细</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="时间" min-width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="触发" width="100">
              <template #default="{ row }">{{ row.trigger_kind || '—' }}</template>
            </el-table-column>
            <el-table-column prop="request_preview" label="请求" min-width="200" show-overflow-tooltip />
            <el-table-column label="注入" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ traceInjected(row) }}</template>
            </el-table-column>
            <el-table-column label="预算" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">{{ traceBudget(row) }}</template>
            </el-table-column>
            <el-table-column label="发送" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ traceSend(row) }}</template>
            </el-table-column>
            <el-table-column label="提供方" width="110">
              <template #default="{ row }">{{ row.provider || '—' }}</template>
            </el-table-column>
            <el-table-column label="本轮总耗时" width="120">
              <template #default="{ row }">{{ row.latency_ms }} ms</template>
            </el-table-column>
            <el-table-column label="错误" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.error || '—' }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="traces.length > RECORD_PAGE_SIZE"
            v-model:current-page="tracePage"
            :page-size="RECORD_PAGE_SIZE"
            :total="traces.length"
            layout="prev, pager, next, total"
            class="table-pager"
            small
          />
        </div>

        <div v-if="activeTab === 'snapshot'" class="panel">
          <div class="panel-header">
            <h3 class="panel-title">模型快照</h3>
            <el-button :icon="Refresh" :loading="modelTraceLoading" @click="loadModelTraces">刷新</el-button>
          </div>
          <div class="tenant-help">
            每次模型调用保存完整请求与返回（含思考内容与工具定义），按会话分目录保留；
            每会话保留数量由「模型快照」配置项控制。
          </div>
          <el-table :data="snapshotRows" row-key="path" empty-text="暂无模型快照">
            <el-table-column label="时间" min-width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="类型" width="110">
              <template #default="{ row }">{{ row.task || row.category }}</template>
            </el-table-column>
            <el-table-column prop="output_title" label="提供方 / 模型" min-width="200" show-overflow-tooltip />
            <el-table-column label="思考摘要" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.reasoning_preview || '—' }}</template>
            </el-table-column>
            <el-table-column prop="turn_id" label="轮次" width="110" show-overflow-tooltip />
            <el-table-column label="耗时" width="100">
              <template #default="{ row }">{{ row.duration_ms }} ms</template>
            </el-table-column>
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ formatBytes(row.size) }}</template>
            </el-table-column>
            <el-table-column label="结果" width="100">
              <template #default="{ row }">{{ row.status === 'failed' ? '失败' : '完成' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openSnapshot(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="modelTraces.length > RECORD_PAGE_SIZE"
            v-model:current-page="snapshotPage"
            :page-size="RECORD_PAGE_SIZE"
            :total="modelTraces.length"
            layout="prev, pager, next, total"
            class="table-pager"
            small
          />
        </div>
      </div>
    </Transition>

    <el-dialog v-model="snapshotDialog" title="模型快照" width="80%" top="6vh">
      <div v-loading="snapshotLoading" class="snapshot-body">
        <template v-if="snapshotDetail">
          <el-descriptions :column="4" border size="small" class="snapshot-meta">
            <el-descriptions-item v-for="item in snapshotMeta" :key="item.label" :label="item.label">
              {{ item.value }}
            </el-descriptions-item>
          </el-descriptions>
          <el-collapse v-model="snapshotOpen" class="snapshot-sections">
            <el-collapse-item
              v-if="snapshotReasoning.length"
              name="reasoning"
              :title="`思考过程 (${snapshotReasoning.length})`"
            >
              <div v-for="(item, index) in snapshotReasoning" :key="index" class="snapshot-item">
                <div class="snapshot-item-head">reasoning #{{ index + 1 }}</div>
                <pre class="snapshot-text">{{ snapshotItemText(item) }}</pre>
              </div>
            </el-collapse-item>
            <el-collapse-item name="request" :title="`请求消息 (${snapshotRequests.length})`">
              <div v-for="(item, index) in snapshotRequests" :key="index" class="snapshot-item">
                <div class="snapshot-item-head">{{ item.role || item.type || 'item' }} #{{ index + 1 }}</div>
                <pre class="snapshot-text">{{ snapshotItemText(item) }}</pre>
              </div>
            </el-collapse-item>
            <el-collapse-item name="output" :title="`模型输出 (${snapshotOutputs.length})`">
              <div v-for="(item, index) in snapshotOutputs" :key="index" class="snapshot-item">
                <div class="snapshot-item-head">{{ item.type || 'item' }} #{{ index + 1 }}</div>
                <pre class="snapshot-text">{{ snapshotItemText(item) }}</pre>
              </div>
            </el-collapse-item>
            <el-collapse-item name="attempts" :title="`调用链 (${snapshotAttempts.length})`">
              <el-table :data="snapshotAttempts" size="small" empty-text="暂无调用记录">
                <el-table-column type="expand">
                  <template #default="{ row }">
                    <pre class="snapshot-text">{{ JSON.stringify(row, null, 2) }}</pre>
                  </template>
                </el-table-column>
                <el-table-column prop="workflow_purpose" label="阶段" min-width="220" />
                <el-table-column prop="workflow_attempt" label="尝试" width="70" />
                <el-table-column prop="provider" label="提供方" min-width="140" show-overflow-tooltip />
                <el-table-column prop="model" label="模型" min-width="160" show-overflow-tooltip />
                <el-table-column label="耗时" width="100">
                  <template #default="{ row }">{{ row.duration_ms }} ms</template>
                </el-table-column>
                <el-table-column label="状态" width="90">
                  <template #default="{ row }">{{ row.status === 'failed' ? '失败' : '完成' }}</template>
                </el-table-column>
                <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip />
              </el-table>
            </el-collapse-item>
            <el-collapse-item v-if="snapshotTools.length" name="tools" :title="`工具定义 (${snapshotTools.length})`">
              <pre class="snapshot-text">{{ JSON.stringify(snapshotTools, null, 2) }}</pre>
            </el-collapse-item>
            <el-collapse-item name="raw" title="原始 JSON">
              <pre class="snapshot-json">{{ snapshotText }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.chat-reply-view {
  padding: 24px 28px 32px;
}

.trace-detail {
  padding: 12px 24px;
}

.trace-timing {
  margin: 0 0 12px;
  color: var(--color-text-secondary);
}

.snapshot-body {
  min-height: 160px;
}

.snapshot-meta {
  margin-bottom: 12px;
}

.snapshot-sections {
  border-top: none;
}

.snapshot-item {
  margin-bottom: 10px;
}

.snapshot-item-head {
  margin-bottom: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.snapshot-text {
  max-height: 320px;
  margin: 0;
  padding: 8px 12px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.snapshot-json {
  max-height: 70vh;
  margin: 0;
  padding: 12px 16px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.session-panel {
  padding-bottom: 16px;
}

.session-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.session-picker {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: min(100%, 420px);
}

.session-eyebrow {
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.session-tags,
.session-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.session-tags {
  padding-bottom: 4px;
}

.session-facts {
  align-items: center;
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.session-settings {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
  gap: 16px;
  align-items: start;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-light);
}

.setting-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.setting-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--color-text);
  font-size: 12px;
  font-weight: 600;
}

.setting-hint {
  min-height: 16px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.setting-frequency {
  max-width: 360px;
}

.setting-frequency :deep(.el-slider) {
  margin: 2px 0 0;
}

.setting-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 20px;
}

.panel-header,
.panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tenant-id {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  word-break: break-all;
}

.tenant-help {
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.table-pager {
  justify-content: flex-end;
  margin-top: 12px;
}

.health-panel {
  margin-bottom: 16px;
}

.panel-header {
  justify-content: space-between;
  flex-wrap: wrap;
}

.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.overview-card {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-bg-tertiary);
}

.overview-wide {
  grid-column: 1 / -1;
}

.overview-title {
  margin: 0 0 12px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.overview-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.overview-title-row .overview-title {
  margin: 0;
}

.detail-list {
  display: grid;
  gap: 10px;
  margin: 0;
}

.detail-list > div {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
}

.detail-list dt {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.detail-list dd {
  margin: 0;
  color: var(--color-text);
  font-size: 12px;
  word-break: break-all;
}

.overview-meta {
  margin-bottom: 12px;
}

.overview-assets {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 24px;
}

.asset-summary {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.asset-empty {
  color: var(--color-text-tertiary);
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 420px;
  padding-right: 4px;
  overflow: auto;
}

.message-row {
  padding: 10px 12px;
  border-left: 3px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  background: var(--color-bg-secondary);
}

.message-row.assistant {
  border-left-color: var(--color-primary);
}

.message-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.message-role {
  color: var(--color-text-secondary);
  font-weight: 600;
}

.message-quote {
  margin: 0 0 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.message-content {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.overview-error {
  margin: 0 0 10px;
  color: var(--color-danger);
  font-size: 12px;
}

.stage-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.stage-error {
  color: var(--color-danger);
}

.preview-block {
  margin-top: 10px;
}

.preview-block p,
.preview-text {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.thinking-text {
  max-height: 260px;
  margin: 4px 0 12px;
  padding: 10px 12px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--color-bg-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.thinking-text.empty {
  color: var(--color-text-tertiary);
  font-family: inherit;
}

.empty-hint {
  padding: 12px 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.reply-tabs {
  width: fit-content;
  max-width: 100%;
  margin-bottom: 20px;
}

.tab-stack {
  display: flex;
  flex-direction: column;
}

.gate-window {
  margin: 16px 0;
}

.gate-distribution {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.block-label {
  margin-bottom: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.rule-tag {
  margin: 0 8px 8px 0;
}

.tab-swap-enter-active,
.tab-swap-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
}

.tab-swap-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.tab-swap-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 1100px) {
  .session-settings {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .setting-actions {
    grid-column: 1 / -1;
    padding-top: 0;
  }
}

@media (max-width: 760px) {
  .overview-grid,
  .overview-assets,
  .session-settings {
    grid-template-columns: minmax(0, 1fr);
  }

  .setting-frequency {
    max-width: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tab-swap-enter-active,
  .tab-swap-leave-active {
    transition: none;
  }
}
</style>
