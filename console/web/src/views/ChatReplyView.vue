<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatTenantPicker from '@/components/ChatTenantPicker.vue'
import { chatApi } from '@/api'
import type {
  ChatTenant,
  ChatPlannerSample,
  ChatReplyGate,
  ChatReplyGateObservation,
  ChatHealth,
  ChatTrace,
  ChatTraceStage,
} from '@/types/api'

const TAB_KEY = 'fxbot-chat-reply-tab'

const tabs = [
  { key: 'planner', label: '规划样本', hint: 'Planner 首轮动作' },
  { key: 'gate', label: '主动接话门控', hint: 'reply 概率与窗口' },
  { key: 'trace', label: '轮次追踪', hint: '每轮注入与耗时' },
] as const

type TabKey = (typeof tabs)[number]['key']
const savedTab = localStorage.getItem(TAB_KEY)
const activeTab = ref<TabKey>(
  tabs.some((tab) => tab.key === savedTab) ? (savedTab as TabKey) : 'planner',
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
const loaded = ref(false)

const RECORD_PAGE_SIZE = 20
const plannerPage = ref(1)
const tracePage = ref(1)
const plannerRows = computed(() =>
  plannerSamples.value.slice((plannerPage.value - 1) * RECORD_PAGE_SIZE, plannerPage.value * RECORD_PAGE_SIZE),
)
const traceRows = computed(() =>
  traces.value.slice((tracePage.value - 1) * RECORD_PAGE_SIZE, tracePage.value * RECORD_PAGE_SIZE),
)

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
    if (tenant.value && !tenants.value.some((item) => item.tenant === tenant.value)) tenant.value = ''
    if (!tenant.value && tenants.value.length) tenant.value = tenants.value[0].tenant
  } catch (error) {
    console.error('加载聊天租户失败', error)
    ElMessage.error('加载聊天租户失败，请重试')
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
  if (!tenant.value) {
    plannerSamples.value = []
    return
  }
  plannerLoading.value = true
  try {
    const result = await chatApi.listPlannerSamples(tenant.value)
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
  if (!tenant.value) {
    replyGate.value = emptyReplyGate()
    return
  }
  gateLoading.value = true
  try {
    replyGate.value = await chatApi.getReplyGate(tenant.value)
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
  if (!tenant.value) {
    traces.value = []
    return
  }
  traceLoading.value = true
  try {
    const result = await chatApi.listTraces(tenant.value)
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

const loadCurrent = async () => {
  if (activeTab.value === 'planner') await loadPlannerSamples()
  else if (activeTab.value === 'gate') await loadReplyGate()
  else await loadTraces()
}

const refresh = async () => {
  await loadTenants()
  await Promise.all([loadHealth(), loadCurrent()])
  loaded.value = true
}

const onTenantChange = async () => {
  await loadCurrent()
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
    <PageHeader title="接话与运行" subtitle="规划、行为、主动接话门控与每轮追踪；按会话独立统计">
      <template #actions>
        <el-button :icon="Refresh" :loading="healthLoading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <div class="panel tenant-panel">
      <div class="tenant-row">
        <span class="tenant-label">会话</span>
        <ChatTenantPicker v-model="tenant" :tenants="tenants" @change="onTenantChange" />
      </div>
      <div class="tenant-help">
        当前 <code class="tenant-id">{{ tenant || '未选择' }}</code>；运行状态是全局的，样本与门控按会话隔离。
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
      </div>
    </Transition>
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

.tenant-panel {
  padding-bottom: 16px;
}

.tenant-row,
.panel-header,
.panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tenant-row {
  display: flex;
}

.tenant-label {
  flex-shrink: 0;
  color: var(--color-text);
  font-weight: 600;
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

@media (prefers-reduced-motion: reduce) {
  .tab-swap-enter-active,
  .tab-swap-leave-active {
    transition: none;
  }
}
</style>
