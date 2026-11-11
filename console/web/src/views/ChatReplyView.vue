<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ChatLineRound, Check, Clock, Collection, Connection, Refresh, Setting } from '@element-plus/icons-vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatTenantPicker from '@/components/ChatTenantPicker.vue'
import ModelTraceBrowser from '@/components/ModelTraceBrowser.vue'
import ModelTraceDetail from '@/components/ModelTraceDetail.vue'
import { chatApi } from '@/api'
import { groupModelTraces } from '@/utils/modelTrace'
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
  { key: 'snapshot', label: '思考过程', hint: '一次处理一条记录，阅读完整思考' },
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
const snapshotDetail = ref<Record<string, any>[]>([])
const loaded = ref(false)
const emptySessionForm = () => ({ persona: '', provider: '', talkOverride: false, talkValue: 100 })
const sessionSettings = ref<ChatSessionSettings | null>(null)
const sessionLoading = ref(false)
const sessionSaving = ref(false)
const settingsOpen = ref(false)
const sessionForm = ref(emptySessionForm())
const sessionDigest = ref<ChatSessionDigest | null>(null)
const digestLoading = ref(false)
const recentMessages = ref<ChatRecentMessage[]>([])
const messagesLoading = ref(false)
const messageList = ref<HTMLElement | null>(null)
let sessionRequest = 0

const RECORD_PAGE_SIZE = 20
const plannerPage = ref(1)
const tracePage = ref(1)
const plannerRows = computed(() =>
  plannerSamples.value.slice((plannerPage.value - 1) * RECORD_PAGE_SIZE, plannerPage.value * RECORD_PAGE_SIZE),
)
const traceRows = computed(() =>
  traces.value.slice((tracePage.value - 1) * RECORD_PAGE_SIZE, tracePage.value * RECORD_PAGE_SIZE),
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
const expressionCursor = computed(() => sessionDigest.value?.cursors.expression || null)
const jargonCursor = computed(() => sessionDigest.value?.cursors.jargon || null)

const percentText = (value: number | null | undefined) => `${Math.round(Number(value ?? 0) * 100)}%`
const sessionSwitchText = computed(() => {
  const value = sessionSettings.value?.proactive.switch
  return value === null || value === undefined ? '未设置' : value ? '已开启' : '已关闭'
})
const hasSessionOverrides = computed(() => {
  const settings = sessionSettings.value
  return !!settings && (!!settings.persona.bound || !!settings.provider.bound || settings.talk_value.bound !== null)
})
const sessionDirty = computed(() => {
  const settings = sessionSettings.value
  if (!settings) return false
  return sessionForm.value.persona !== settings.persona.bound
    || sessionForm.value.provider !== settings.provider.bound
    || sessionForm.value.talkOverride !== (settings.talk_value.bound !== null)
    || (sessionForm.value.talkOverride
      && sessionForm.value.talkValue !== Math.round((settings.talk_value.bound ?? 0) * 100))
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
    if (tenant.value === target) {
      recentMessages.value = result.items || []
      // 列表按时间正序，最新消息在底部；加载后滚动到底部，避免停在旧消息上
      await nextTick()
      if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
    }
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
  } catch (error) {
    console.error('加载模型快照失败', error)
    ElMessage.error('加载模型快照失败，请重试')
  } finally {
    modelTraceLoading.value = false
  }
}

let snapshotRequest = 0
const openSnapshot = async (row: ChatModelTrace) => {
  const request = ++snapshotRequest
  const target = tenant.value
  const group = groupModelTraces(modelTraces.value).find((item) => item.paths.includes(row.path))
  const paths = group?.paths || [row.path]
  snapshotDialog.value = true
  snapshotLoading.value = true
  snapshotDetail.value = []
  try {
    const results = await Promise.allSettled(paths.map((path) => chatApi.getModelTrace(path)))
    if (request !== snapshotRequest || target !== tenant.value) return
    snapshotDetail.value = results.flatMap((result) => result.status === 'fulfilled' ? [result.value.snapshot] : [])
    if (!snapshotDetail.value.length) {
      ElMessage.error('读取模型快照失败，请重试')
      snapshotDialog.value = false
    }
  } finally {
    if (request === snapshotRequest) snapshotLoading.value = false
  }
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
  snapshotRequest++
  snapshotDialog.value = false
  snapshotDetail.value = []
  snapshotLoading.value = false
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
    <PageHeader title="接话与运行" subtitle="观察对话，了解思考，调整每个会话的回应方式。">
      <template #actions>
        <el-button :icon="Refresh" :loading="healthLoading" @click="refresh">刷新数据</el-button>
      </template>
    </PageHeader>

    <div v-loading="sessionLoading" class="panel session-panel">
      <div class="session-head">
        <div class="session-picker">
          <span class="session-eyebrow">当前会话</span>
          <ChatTenantPicker v-model="tenant" :tenants="tenants" @change="onTenantChange" />
        </div>
        <div class="session-controls">
          <span v-if="sessionDirty" class="unsaved-mark">有未保存的修改</span>
          <el-button
            v-if="tenant"
            :icon="Setting"
            :type="settingsOpen ? 'primary' : 'default'"
            :plain="settingsOpen"
            :aria-expanded="settingsOpen"
            aria-controls="session-settings"
            @click="settingsOpen = !settingsOpen"
          >
            会话设置 <el-icon class="settings-chevron" :class="{ expanded: settingsOpen }"><ArrowDown /></el-icon>
          </el-button>
        </div>
      </div>

      <div v-if="tenant" class="session-summary">
        <div class="summary-item">
          <span>人格</span><strong>{{ sessionSettings?.persona.effective || '—' }}</strong>
        </div>
        <div class="summary-item">
          <span>模型</span><strong :title="sessionSettings?.provider.effective">{{ sessionSettings?.provider.effective || '未指定' }}</strong>
        </div>
        <div class="summary-item">
          <span>回复频率</span><strong>{{ percentText(sessionSettings?.talk_value.effective) }}</strong>
        </div>
        <div class="summary-item">
          <span>主动接话</span><strong>{{ sessionSwitchText }}</strong>
        </div>
      </div>
      <div v-else class="tenant-help">选择会话后可查看详情并设置本会话的人格、Provider 与回复频率。</div>

      <div v-if="tenant" v-show="settingsOpen" id="session-settings" class="session-editor">
      <div class="editor-heading">
        <div><h3>调整会话设置</h3><p>仅影响当前会话，未覆盖的选项继续跟随全局。</p></div>
        <span v-if="sessionDirty" class="unsaved-mark">尚未保存</span>
      </div>
      <div class="session-settings">
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
            <label>模型提供方</label>
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

      </div>
      <div class="editor-footer">
        <p>切换人格会清空短期历史，学习记录和长期记忆不受影响。</p>
        <div class="setting-actions">
          <el-button :loading="sessionSaving" :disabled="sessionLoading || !hasSessionOverrides" @click="resetSessionSettings">恢复全局</el-button>
          <el-button type="primary" :icon="Check" :loading="sessionSaving" :disabled="sessionLoading || !sessionSettings || !sessionDirty" @click="saveSessionSettings">保存修改</el-button>
        </div>
      </div>
      </div>
    </div>

    <div class="runtime-bar" aria-label="运行状态">
      <span class="runtime-label"><el-icon><Connection /></el-icon>运行状态</span>
      <span class="runtime-chip" :class="{ ok: health?.enabled }">
        <i />{{ health ? (health.enabled ? '对话已启用' : '对话未启用') : '对话状态未知' }}
      </span>
      <span class="runtime-chip" :class="{ ok: health?.storage_ready }">
        <i />{{ health ? (health.storage_ready ? '存储就绪' : '存储未就绪') : '存储状态未知' }}
      </span>
      <span class="runtime-chip" :class="{ ok: health?.scheduler_ready }">
        <i />{{ health ? (health.scheduler_ready ? '调度运行中' : '调度未加载') : '调度状态未知' }}
      </span>
      <span class="runtime-chip"><i class="is-info" />{{ health?.online_bots ?? '—' }} 个 Bot 在线</span>
      <span v-if="health?.degraded_reason" class="runtime-error">{{ health.degraded_reason }}</span>
    </div>

    <PillTabs v-if="loaded" v-model="activeTab" :items="tabs" class="reply-tabs" />

    <Transition name="tab-swap" mode="out-in">
      <div :key="activeTab" class="tab-stack">
        <div v-if="activeTab === 'overview'" class="overview-panel">
          <div v-if="tenant" class="overview-metrics">
            <MetricCard
              label="会话消息"
              :value="currentTenant?.messages ?? 0"
              hint="当前保留的对话记录"
              :icon="ChatLineRound"
            />
            <MetricCard
              label="已发送轮次"
              :value="`${turnCounts.sent} / ${turnCounts.total}`"
              :hint="turnCounts.issues ? `${turnCounts.issues} 轮需要关注` : '暂无异常轮次'"
              :icon="Check"
              :tone="turnCounts.issues ? 'warning' : 'success'"
            />
            <MetricCard
              label="最近模型耗时"
              :value="latestSnapshot ? `${(latestSnapshot.duration_ms / 1000).toFixed(1)} s` : '—'"
              :hint="latestSnapshot?.model_name || '等待首次模型调用'"
              :icon="Clock"
              tone="info"
            />
            <MetricCard
              label="学习资产"
              :value="jargonCount + (sessionDigest?.expressions.total ?? 0)"
              :hint="`黑话 ${jargonCount} · 表达 ${sessionDigest?.expressions.total ?? 0}`"
              :icon="Collection"
            />
          </div>

          <div v-if="!tenant" class="overview-placeholder">
            <el-icon><ChatLineRound /></el-icon>
            <p>先选择一个会话，查看最近对话、学习资产与模型思考。</p>
          </div>

          <div v-if="tenant" class="overview-grid">
            <section class="overview-card facts-card">
              <div class="facts-grid">
                <div class="facts-column">
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
                      <dt>最近活跃</dt>
                      <dd>{{ formatTime(digestState?.last_active_at) }}</dd>
                    </div>
                    <div>
                      <dt>清空</dt>
                      <dd>
                        {{ digestState?.generation ? `${digestState.generation} 次` : '未清空' }}
                        <span v-if="digestState?.cleared_at">· 上次 {{ formatTime(digestState.cleared_at) }}</span>
                      </dd>
                    </div>
                    <div>
                      <dt>学习 / 使用</dt>
                      <dd>{{ currentTenant?.learn_count ?? 0 }} / {{ currentTenant?.use_count ?? 0 }}</dd>
                    </div>
                    <div>
                      <dt>完整 ID</dt>
                      <dd><code class="tenant-id">{{ tenant || '未选择' }}</code></dd>
                    </div>
                  </dl>
                </div>

                <div class="facts-column">
                  <h4 class="overview-title">学习资产</h4>
                  <dl class="detail-list">
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
                    <div>
                      <dt>学习游标</dt>
                      <dd v-if="expressionCursor || jargonCursor">
                        表达落后 {{ expressionCursor?.backlog ?? '—' }} 条
                        <template v-if="expressionCursor?.fail_count">（失败 {{ expressionCursor.fail_count }}）</template>
                        · 黑话落后 {{ jargonCursor?.backlog ?? '—' }} 条
                        <template v-if="jargonCursor?.fail_count">（失败 {{ jargonCursor.fail_count }}）</template>
                      </dd>
                      <dd v-else class="asset-empty">尚未建立游标</dd>
                    </div>
                  </dl>
                </div>
              </div>
            </section>

            <section class="overview-card messages-card">
              <div class="overview-title-row">
                <h4 class="overview-title">最近对话 <span class="section-count">{{ recentMessages.length }}</span></h4>
                <el-button :icon="Refresh" link type="primary" :loading="messagesLoading" @click="loadRecentMessages">
                  刷新
                </el-button>
              </div>
              <div v-if="recentMessages.length" class="message-viewport">
                <div ref="messageList" class="message-list">
                  <div
                    v-for="(message, index) in recentMessages"
                    :key="index"
                    class="message-row"
                    :class="message.role"
                  >
                    <div class="message-avatar" aria-hidden="true">{{ message.role === 'assistant' ? 'AI' : (message.sender_name || message.sender_id || '用').slice(0, 1) }}</div>
                    <div class="message-body">
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
                      <div class="message-bubble">
                        <p v-if="message.quote" class="message-quote">引用：{{ message.quote }}</p>
                        <p class="message-content">{{ message.content }}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="empty-hint">{{ messagesLoading ? '加载中…' : '暂无消息记录' }}</div>
            </section>

            <section class="overview-card turn-card">
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
                <p v-if="traceTiming(latestTrace)" class="trace-timing-line">{{ traceTiming(latestTrace) }}</p>
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

            <section class="overview-card thinking-card">
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
                <span class="block-label">思考摘要</span>
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

        <ModelTraceBrowser
          v-if="activeTab === 'snapshot'"
          :tenant="tenant"
          :items="modelTraces"
          :loading="modelTraceLoading"
        />
      </div>
    </Transition>

    <el-dialog v-model="snapshotDialog" title="思考过程" width="min(1120px, calc(100vw - 32px))" top="5vh">
      <div v-loading="snapshotLoading" class="snapshot-body">
        <ModelTraceDetail v-if="snapshotDetail.length" :snapshots="snapshotDetail" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.chat-reply-view {
  padding: 24px 28px 32px;
}

.chat-reply-view > .panel, .tab-stack > .panel {
  border-radius: var(--radius-xl);
}

.session-panel {
  padding: 22px 24px 0;
  margin-bottom: 0;
  background:
    radial-gradient(circle at 100% 0%, var(--color-primary-soft) 0%, transparent 42%),
    var(--color-bg-secondary);
  box-shadow: var(--shadow-sm);
}

.session-panel > .tenant-help {
  padding-bottom: 18px;
}

.session-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.session-picker {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: min(100%, 300px);
  max-width: 600px;
}

.session-picker :deep(.tenant-trigger) {
  min-height: 58px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.session-picker :deep(.tenant-current-name) {
  font-size: 18px;
  letter-spacing: -0.02em;
}

.session-picker :deep(.tenant-current-meta) {
  font-size: 11px;
}

.session-picker :deep(.tenant-trigger:focus-visible) {
  outline: 2px solid var(--color-primary);
  outline-offset: 4px;
}

.session-picker :deep(.tenant-pop) {
  min-width: min(340px, calc(100vw - 68px));
}

.session-eyebrow {
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.session-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.settings-chevron {
  margin-left: 8px;
  transition: transform 0.18s ease;
}

.settings-chevron.expanded {
  transform: rotate(180deg);
}

.unsaved-mark {
  color: var(--color-warning);
  font-size: 12px;
}

.session-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
  padding: 16px 0;
  border-top: 1px solid var(--color-border-light);
}

.summary-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  padding: 6px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-bg-tertiary);
}

.summary-item span {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.summary-item strong {
  overflow: hidden;
  max-width: 220px;
  color: var(--color-text);
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-editor {
  margin: 0 -24px;
  padding: 22px 24px 18px;
  border-top: 1px solid var(--color-border);
  border-radius: 0 0 var(--radius-xl) var(--radius-xl);
  background: var(--color-bg-card);
}

.editor-heading, .editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.editor-heading h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.editor-heading p, .editor-footer p {
  margin: 6px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.7;
}

.session-settings {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
  align-items: start;
  margin-top: 22px;
}

.setting-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-bg-tertiary);
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
  line-height: 1.6;
}

.setting-frequency :deep(.el-slider) {
  margin: 2px 0 0;
}

.editor-footer {
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-light);
}

.setting-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}

.setting-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.runtime-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 14px 2px 18px;
  font-size: 11px;
}

.runtime-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 4px;
  color: var(--color-text-tertiary);
}

.runtime-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--color-border-light);
  border-radius: 999px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}

.runtime-chip i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
}

.runtime-chip.ok {
  border-color: color-mix(in srgb, var(--color-success) 24%, transparent);
  background: color-mix(in srgb, var(--color-success) 10%, transparent);
  color: var(--color-text-secondary);
}

.runtime-chip.ok i {
  background: var(--color-success);
}

.runtime-chip i.is-info {
  background: var(--color-primary);
}

.runtime-error {
  color: var(--color-danger);
}

.panel-header, .panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.panel-header {
  justify-content: space-between;
  flex-wrap: wrap;
}

.tenant-id {
  padding: 2px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  overflow-wrap: anywhere;
}

.tenant-help {
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.8;
}

.table-pager {
  justify-content: flex-end;
  margin-top: 16px;
}

.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.7;
}

.overview-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.overview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 64px 20px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-xl);
  color: var(--color-text-tertiary);
}

.overview-placeholder .el-icon {
  font-size: 26px;
}

.overview-placeholder p {
  margin: 0;
  font-size: 12px;
}

.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  grid-template-areas:
    'facts facts'
    'messages thinking'
    'messages trace';
  gap: 20px;
}

.overview-card {
  min-width: 0;
  padding: 20px 22px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-bg-card);
  box-shadow: var(--shadow-sm);
}

.facts-card {
  grid-area: facts;
}

.facts-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}

.facts-column {
  min-width: 0;
}

.facts-column + .facts-column {
  padding-left: 32px;
  border-left: 1px solid var(--color-border-light);
}

.messages-card {
  display: flex;
  flex-direction: column;
  grid-area: messages;
}

.turn-card {
  grid-area: trace;
}

.thinking-card {
  grid-area: thinking;
}

.overview-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 16px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.overview-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.overview-title-row .overview-title {
  margin: 0;
}

.section-count {
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-weight: 500;
}

.detail-list {
  display: grid;
  gap: 14px;
  margin: 0;
}

.detail-list > div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
}

.detail-list dt {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.detail-list dd {
  margin: 0;
  color: var(--color-text);
  font-size: 12px;
  overflow-wrap: anywhere;
  line-height: 1.7;
}

.overview-meta {
  margin-bottom: 16px;
}

.asset-summary {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.asset-empty {
  color: var(--color-text-tertiary);
}

.message-viewport {
  position: relative;
  flex: 1;
  min-height: 360px;
}

.message-viewport::before {
  content: '';
  position: absolute;
  z-index: 1;
  top: 0;
  right: 0;
  left: 0;
  height: 20px;
  background: linear-gradient(var(--color-bg-card), transparent);
  pointer-events: none;
}

.message-list {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 6px;
  overflow: auto;
  scrollbar-width: thin;
}

.message-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.message-avatar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--color-border-light);
  border-radius: 50%;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 600;
}

.message-row.assistant .message-avatar {
  border-color: transparent;
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.message-body {
  flex: 1;
  min-width: 0;
}

.message-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.message-role {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.message-bubble {
  display: inline-block;
  max-width: min(76ch, 100%);
  padding: 9px 13px;
  border: 1px solid var(--color-border-light);
  border-radius: 4px 12px 12px 12px;
  background: var(--color-bg-tertiary);
}

.message-row.assistant .message-bubble {
  border-color: transparent;
  background: var(--color-primary-soft);
}

.message-quote {
  margin: 0 0 6px;
  padding-left: 8px;
  border-left: 2px solid var(--color-border);
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.message-content {
  margin: 0;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.overview-error {
  color: var(--color-danger);
  font-size: 12px;
}

.trace-timing-line {
  margin: 0 0 14px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.7;
}

.preview-block {
  margin-top: 14px;
}

.preview-block p, .preview-text {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.thinking-text {
  max-height: 260px;
  margin: 8px 0 18px;
  padding: 14px 16px;
  overflow: auto;
  border-left: 2px solid var(--color-primary);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  background: var(--color-bg-tertiary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.thinking-text.empty {
  color: var(--color-text-tertiary);
}

.empty-hint {
  padding: 34px 12px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.reply-tabs {
  width: fit-content;
  max-width: 100%;
  margin-bottom: 24px;
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
  font-size: 11px;
  font-weight: 600;
}

.rule-tag {
  margin: 0 8px 8px 0;
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


.tab-swap-enter-active, .tab-swap-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
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
  .chat-reply-view {
    padding: 20px 22px 26px;
  }
  .session-summary {
    gap: 8px;
  }
  .overview-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .overview-grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: 'facts' 'messages' 'thinking' 'trace';
  }
  .facts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .facts-column + .facts-column {
    margin-top: 20px;
    padding-top: 20px;
    padding-left: 0;
    border-top: 1px solid var(--color-border-light);
    border-left: 0;
  }
}

@media (max-width: 760px) {
  .chat-reply-view {
    padding: 16px;
  }
  .session-panel {
    padding: 18px 18px 0;
  }
  .session-editor {
    margin-inline: -18px;
    padding-inline: 18px;
  }
  .session-head {
    gap: 14px;
  }
  .session-picker {
    flex-basis: 100%;
  }
  .session-controls {
    margin-left: auto;
  }
  .summary-item strong {
    max-width: 150px;
  }
  .runtime-bar {
    gap: 6px;
  }
  .session-settings {
    grid-template-columns: minmax(0, 1fr);
  }
  .editor-footer {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
  }
  .setting-actions {
    align-self: flex-end;
  }
  .overview-card {
    padding: 18px;
  }
  .trace-detail {
    padding: 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tab-swap-enter-active, .tab-swap-leave-active, .settings-chevron {
    transition: none;
  }
}
</style>
