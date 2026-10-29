<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound,
  ChatLineRound,
  DataLine,
  Message,
  Refresh,
  Search,
  TrendCharts,
} from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import MetricCard from '@/components/MetricCard.vue'
import PillTabs from '@/components/PillTabs.vue'
import TrendChart from '@/components/TrendChart.vue'
import { statsApi } from '@/api'
import type { ModelUsage, ModelUsageTask, StatsHistoryDay, StatsToday } from '@/types/api'

const RANGE_KEY = 'fxbot-stats-range'
const ranges = [7, 14, 30]
const rangeItems = ranges.map((value) => ({ key: String(value), label: `${value} 天` }))
const USAGE_RANGE_KEY = 'fxbot-stats-usage-range'
const usageRanges = [24, 168]
const usageItems = usageRanges.map((value) => ({ key: String(value), label: value === 24 ? '24 小时' : '7 天' }))

const TASK_LABELS: Record<string, string> = {
  reply: '回复',
  planner: '规划',
  style: '群风格',
  extract: '抽取',
  learner: '表达学习',
  vision: '图片描述',
  memory: '长期记忆',
  utils: '通用工具',
  emoji: '表情管理',
}

const loading = ref(false)
const stats = ref<StatsToday>({ bots: {} })
const history = ref<StatsHistoryDay[]>([])
const savedRange = Number(localStorage.getItem(RANGE_KEY))
const range = ref<number>(ranges.includes(savedRange) ? savedRange : 14)
const savedUsageRange = Number(localStorage.getItem(USAGE_RANGE_KEY))
const usageHours = ref<number>(usageRanges.includes(savedUsageRange) ? savedUsageRange : 24)
const usageLoading = ref(false)
const usage = ref<ModelUsage>({ hours: 24, request_count: 0, tasks: [] })

// 窄屏没有横向空间，列要真删掉而不是用 CSS 藏（el-table 的 colgroup 仍会占位）
const compactQuery = window.matchMedia('(max-width: 720px)')
const compact = ref(compactQuery.matches)
const syncCompact = (event: MediaQueryListEvent) => {
  compact.value = event.matches
}
compactQuery.addEventListener('change', syncCompact)
onBeforeUnmount(() => compactQuery.removeEventListener('change', syncCompact))

const totalSent = computed(() =>
  Object.values(stats.value.bots).reduce((total, bot) => total + bot.total_sent, 0),
)
const totalGroups = computed(() =>
  Object.values(stats.value.bots).reduce((total, bot) => total + (bot.group?.count || 0), 0),
)
const totalPrivate = computed(() =>
  Object.values(stats.value.bots).reduce((total, bot) => total + (bot.private?.count || 0), 0),
)
const groupCount = computed(() => new Set(Object.keys(aggregateTargets('group'))).size)
const privateCount = computed(() => new Set(Object.keys(aggregateTargets('private'))).size)

function aggregateTargets(kind: 'group' | 'private'): Record<string, number> {
  const merged: Record<string, number> = {}
  Object.values(stats.value.bots).forEach((bot) => {
    const targets = bot[kind]?.targets
    if (!targets) return
    Object.entries(targets).forEach(([id, count]) => {
      merged[id] = (merged[id] || 0) + count
    })
  })
  return merged
}

const LEADERBOARD_SIZE = 6

const topTargets = (kind: 'group' | 'private', limit = LEADERBOARD_SIZE) =>
  Object.entries(aggregateTargets(kind))
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([id, count]) => ({ id, count }))

const topGroups = computed(() => topTargets('group'))
const topPrivate = computed(() => topTargets('private'))
const maxGroupTarget = computed(() => Math.max(1, ...topGroups.value.map((item) => item.count)))
const maxPrivateTarget = computed(() => Math.max(1, ...topPrivate.value.map((item) => item.count)))

const dialogKind = ref<'group' | 'private' | null>(null)
const dialogFilter = ref('')
const dialogOpen = computed({
  get: () => dialogKind.value !== null,
  set: (value: boolean) => {
    if (!value) dialogKind.value = null
  },
})
const dialogTitle = computed(() =>
  dialogKind.value === 'group' ? `全部群聊 · ${groupCount.value} 个` : `全部私聊 · ${privateCount.value} 人`,
)
const dialogRows = computed(() => {
  const kind = dialogKind.value
  if (!kind) return []
  const keyword = dialogFilter.value.trim()
  return Object.entries(aggregateTargets(kind))
    .filter(([id]) => !keyword || id.includes(keyword))
    .sort((a, b) => b[1] - a[1])
    .map(([id, count]) => ({ id, count }))
})
const dialogMax = computed(() => Math.max(1, ...dialogRows.value.map((item) => item.count)))

const openAllTargets = (kind: 'group' | 'private') => {
  dialogFilter.value = ''
  dialogKind.value = kind
}

const trendItems = computed(() => history.value.slice(-range.value))
const trendTotal = computed(() => trendItems.value.reduce((total, item) => total + item.sent, 0))
const trendAverage = computed(() =>
  trendItems.value.length ? Math.round(trendTotal.value / trendItems.value.length) : 0,
)
const trendPeak = computed(() => Math.max(0, ...trendItems.value.map((item) => item.sent)))

type BotRow = {
  id: string
  total: number
  group: number
  private: number
  groupTargets: Record<string, number>
  privateTargets: Record<string, number>
  groupCount: number
  privateCount: number
}

const bots = computed<BotRow[]>(() =>
  Object.entries(stats.value.bots)
    .map(([id, bot]) => ({
      id,
      total: bot.total_sent,
      group: bot.group?.count || 0,
      private: bot.private?.count || 0,
      groupTargets: bot.group?.targets || {},
      privateTargets: bot.private?.targets || {},
      groupCount: Object.keys(bot.group?.targets || {}).length,
      privateCount: Object.keys(bot.private?.targets || {}).length,
    }))
    .sort((a, b) => b.total - a.total),
)

const botTopTargets = (targets: Record<string, number>, limit = 5) =>
  Object.entries(targets)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([id, count]) => ({ id, count }))

const botDetail = (bot: BotRow) =>
  [
    { key: 'group', label: '群聊', total: bot.groupCount, sent: bot.group, items: botTopTargets(bot.groupTargets) },
    {
      key: 'private',
      label: '私聊',
      total: bot.privateCount,
      sent: bot.private,
      items: botTopTargets(bot.privateTargets),
    },
  ].map((entry) => ({ ...entry, max: Math.max(1, ...entry.items.map((item) => item.count)) }))

const selectRange = (value: number) => {
  range.value = value
  localStorage.setItem(RANGE_KEY, String(value))
}

const formatNumber = (value: number) => value.toLocaleString('zh-CN')

const shareOf = (value: number) => (totalSent.value ? Math.round((value / totalSent.value) * 100) : 0)

const taskLabel = (task: string) => TASK_LABELS[task] || task

const formatPercent = (value: number) => `${(value * 100).toFixed(1)}%`

const formatDuration = (seconds: number | null) => {
  if (seconds === null) return '—'
  return seconds >= 1 ? `${seconds.toFixed(2)} s` : `${Math.round(seconds * 1000)} ms`
}

const sortFailureRate = (a: ModelUsageTask, b: ModelUsageTask) => a.failure_rate - b.failure_rate
const sortDuration = (a: ModelUsageTask, b: ModelUsageTask) =>
  (a.avg_duration ?? Number.POSITIVE_INFINITY) - (b.avg_duration ?? Number.POSITIVE_INFINITY)

const loadUsage = async () => {
  usageLoading.value = true
  try {
    usage.value = await statsApi.getModelUsage(usageHours.value)
  } catch (e: any) {
    ElMessage.error('加载模型用量失败: ' + (e?.message || e))
  } finally {
    usageLoading.value = false
  }
}

const selectUsageRange = (value: number) => {
  usageHours.value = value
  localStorage.setItem(USAGE_RANGE_KEY, String(value))
  loadUsage()
}

const loadStats = async () => {
  loading.value = true
  try {
    const [today, hist] = await Promise.all([
      statsApi.getToday(),
      statsApi.getHistory(30).catch(() => ({ days: 0, items: [] as StatsHistoryDay[] })),
    ])
    stats.value = today
    history.value = hist.items || []
  } catch (e: any) {
    ElMessage.error('加载统计失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
  loadUsage()
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div class="stats-view">
    <PageHeader title="消息统计" subtitle="今日发送明细、近 30 天趋势与模型调用（本地记录，不上传）">
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="loadStats">刷新</el-button>
      </template>
    </PageHeader>

    <div class="metric-grid">
      <MetricCard
        label="今日发送"
        :value="formatNumber(totalSent)"
        hint="所有 Bot 合计"
        :icon="Message"
      />
      <MetricCard
        label="群聊消息"
        :value="formatNumber(totalGroups)"
        :hint="`${groupCount} 个群`"
        :icon="ChatDotRound"
        tone="info"
      />
      <MetricCard
        label="私聊消息"
        :value="formatNumber(totalPrivate)"
        :hint="`${privateCount} 位用户`"
        :icon="ChatLineRound"
        tone="success"
      />
      <MetricCard
        :label="`近 ${range} 天日均`"
        :value="formatNumber(trendAverage)"
        hint="按自然日统计"
        :icon="DataLine"
        tone="warning"
      />
      <MetricCard
        :label="`近 ${range} 天峰值`"
        :value="formatNumber(trendPeak)"
        hint="单日最高发送"
        :icon="TrendCharts"
        tone="danger"
      />
    </div>

    <div class="stats-grid">
      <section class="panel trend-panel">
        <div class="panel-header">
          <div class="panel-heading">
            <h3 class="panel-title">消息趋势</h3>
            <p class="panel-sub">近 {{ range }} 天共 {{ formatNumber(trendTotal) }} 条</p>
          </div>
          <PillTabs
            :model-value="String(range)"
            :items="rangeItems"
            size="small"
            @update:model-value="selectRange(Number($event))"
          />
        </div>
        <TrendChart :items="trendItems" :height="216" />
      </section>

      <section class="panel">
        <div class="panel-header">
          <h3 class="panel-title">今日构成</h3>
        </div>

        <div class="split-bar">
          <span
            class="split-segment is-group"
            :style="{ width: `${totalSent ? (totalGroups / totalSent) * 100 : 0}%` }"
          />
          <span class="split-segment is-private" />
        </div>
        <div class="split-legend">
          <span><i class="dot is-group" />群聊 {{ formatNumber(totalGroups) }}</span>
          <span><i class="dot is-private" />私聊 {{ formatNumber(totalPrivate) }}</span>
        </div>

        <div class="target-block">
          <div class="target-heading">
            <span>活跃群聊 <em>{{ groupCount }} 个</em></span>
            <button
              v-if="groupCount > topGroups.length"
              type="button"
              class="target-more"
              @click="openAllTargets('group')"
            >
              查看全部
            </button>
          </div>
          <div v-if="topGroups.length" class="target-list">
            <div v-for="item in topGroups" :key="item.id" class="target-row">
              <span class="target-name" :title="item.id">{{ item.id }}</span>
              <span class="target-bar"><i :style="{ width: `${(item.count / maxGroupTarget) * 100}%` }" /></span>
              <span class="target-count">{{ item.count }}</span>
            </div>
          </div>
          <div v-else class="empty-line">暂无群聊消息</div>
        </div>

        <div class="target-block">
          <div class="target-heading">
            <span>活跃私聊 <em>{{ privateCount }} 人</em></span>
            <button
              v-if="privateCount > topPrivate.length"
              type="button"
              class="target-more"
              @click="openAllTargets('private')"
            >
              查看全部
            </button>
          </div>
          <div v-if="topPrivate.length" class="target-list">
            <div v-for="item in topPrivate" :key="item.id" class="target-row">
              <span class="target-name" :title="item.id">{{ item.id }}</span>
              <span class="target-bar is-private"><i :style="{ width: `${(item.count / maxPrivateTarget) * 100}%` }" /></span>
              <span class="target-count">{{ item.count }}</span>
            </div>
          </div>
          <div v-else class="empty-line">暂无私聊消息</div>
        </div>
      </section>
    </div>

    <section class="panel">
      <div class="panel-header">
        <h3 class="panel-title">Bot 明细</h3>
        <span class="panel-sub">{{ bots.length }} 个 Bot</span>
      </div>

      <el-table v-if="bots.length" :data="bots" row-key="id" class="bot-table" empty-text="今日暂无发送记录">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="bot-detail">
              <div v-for="kind in botDetail(row)" :key="kind.key" class="bot-detail-col">
                <div class="bot-detail-head">
                  <span>{{ kind.label }} Top 5</span>
                  <span class="bot-detail-meta">共 {{ kind.total }} 个 · {{ formatNumber(kind.sent) }} 条</span>
                </div>
                <div v-if="kind.items.length" class="target-list">
                  <div v-for="item in kind.items" :key="item.id" class="target-row">
                    <span class="target-name" :title="item.id">{{ item.id }}</span>
                    <span class="target-bar" :class="{ 'is-private': kind.key === 'private' }">
                      <i :style="{ width: `${(item.count / kind.max) * 100}%` }" />
                    </span>
                    <span class="target-count">{{ item.count }}</span>
                  </div>
                </div>
                <div v-else class="empty-line">无{{ kind.label }}消息</div>
                <div v-if="kind.total > kind.items.length" class="bot-detail-more">
                  仅列出发送最多的 {{ kind.items.length }} 个
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="Bot" width="150">
          <template #default="{ row }">
            <span class="bot-name">Bot {{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="total" label="今日发送" width="104" align="right" sortable>
          <template #default="{ row }">
            <span class="bot-total">{{ formatNumber(row.total) }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!compact" prop="group" label="群聊" width="100" align="right" sortable />
        <el-table-column v-if="!compact" prop="private" label="私聊" width="100" align="right" sortable />
        <el-table-column v-if="!compact" prop="groupCount" label="活跃群" width="110" align="right" sortable />
        <el-table-column
          v-if="!compact"
          prop="privateCount"
          label="活跃用户"
          width="110"
          align="right"
          sortable
        />
        <el-table-column v-if="!compact" label="占比" min-width="170">
          <template #default="{ row }">
            <div class="bot-share">
              <span class="bot-share-bar"><i :style="{ width: `${shareOf(row.total)}%` }" /></span>
              <span class="bot-share-value">{{ shareOf(row.total) }}%</span>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else :image-size="72" description="今日暂无发送记录" />
    </section>

    <section class="panel usage-panel">
      <div class="panel-header">
        <div class="panel-heading">
          <h3 class="panel-title">模型用量</h3>
          <p class="panel-sub">
            近 {{ usageHours === 24 ? '24 小时' : '7 天' }}共 {{ formatNumber(usage.request_count) }} 次调用；汇总保留 7 天
          </p>
        </div>
        <PillTabs
          :model-value="String(usageHours)"
          :items="usageItems"
          size="small"
          @update:model-value="selectUsageRange(Number($event))"
        />
      </div>

      <el-table
        v-if="usage.tasks.length"
        v-loading="usageLoading"
        :data="usage.tasks"
        row-key="task_name"
        class="usage-table"
        empty-text="暂无模型调用"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="usage-models">
              <div v-for="model in row.models" :key="`${model.model_name}-${model.provider_name}`" class="usage-model-row">
                <span class="usage-model-name">{{ model.model_name || '未知模型' }}</span>
                <span class="usage-model-provider">{{ model.provider_name || '默认 Provider' }}</span>
                <span class="usage-model-count">{{ formatNumber(model.request_count) }} 次</span>
                <span class="usage-model-rate">{{ formatPercent(model.failure_rate) }} 失败</span>
                <span class="usage-model-time">{{ formatDuration(model.avg_duration) }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="任务" min-width="120">
          <template #default="{ row }">
            <span class="usage-task">{{ taskLabel(row.task_name) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="request_count" label="调用" width="110" align="right" sortable />
        <el-table-column label="失败率" width="110" align="right" sortable :sort-method="sortFailureRate">
          <template #default="{ row }">{{ formatPercent(row.failure_rate) }}</template>
        </el-table-column>
        <el-table-column label="平均耗时" width="120" align="right" sortable :sort-method="sortDuration">
          <template #default="{ row }">{{ formatDuration(row.avg_duration) }}</template>
        </el-table-column>
      </el-table>

      <el-empty v-else :image-size="72" description="暂无模型调用记录" />
    </section>

    <el-dialog v-model="dialogOpen" :title="dialogTitle" width="520px" class="target-dialog">
      <el-input
        v-model="dialogFilter"
        :prefix-icon="Search"
        placeholder="按 ID 搜索"
        clearable
        class="dialog-search"
      />
      <div class="dialog-list">
        <div v-for="item in dialogRows" :key="item.id" class="target-row">
          <span class="target-name" :title="item.id">{{ item.id }}</span>
          <span class="target-bar" :class="{ 'is-private': dialogKind === 'private' }">
            <i :style="{ width: `${(item.count / dialogMax) * 100}%` }" />
          </span>
          <span class="target-count">{{ item.count }}</span>
        </div>
        <div v-if="!dialogRows.length" class="empty-line">没有匹配的目标</div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.stats-view {
  padding: 24px 28px 32px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.stats-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, 1fr);
  gap: 20px;

  .panel {
    margin-bottom: 20px;
  }
}

.panel-heading {
  min-width: 0;
}

.panel-sub {
  margin: 4px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.panel-header .panel-sub {
  margin: 0;
}

.split-bar {
  display: flex;
  gap: 2px;
  height: 10px;
  margin-top: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-bg-tertiary);
}

.split-segment {
  display: block;
  height: 100%;

  &.is-group {
    background: var(--color-primary);
  }

  &.is-private {
    flex: 1;
    background: var(--color-info);
  }
}

.split-legend {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;

  span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-primary);

  &.is-private {
    background: var(--color-info);
  }
}

.target-block {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-light);
}

.target-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;

  em {
    color: var(--color-text-tertiary);
    font-style: normal;
    font-weight: 500;
  }
}

.target-more {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-primary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}

.target-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.target-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.target-name {
  overflow: hidden;
  flex: none;
  width: 96px;
  color: var(--color-text-secondary);
  font-family: ui-monospace, 'JetBrains Mono', Monaco, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-bar {
  flex: 1;
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-bg-tertiary);

  i {
    display: block;
    min-width: 3px;
    height: 100%;
    border-radius: inherit;
    background: var(--color-primary);
  }

  &.is-private i {
    background: var(--color-info);
  }
}

.target-count {
  flex: none;
  width: 44px;
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.empty-line {
  padding: 10px 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.bot-table {
  font-variant-numeric: tabular-nums;
}

.bot-name {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.bot-total {
  color: var(--color-primary);
  font-weight: 600;
}

.bot-share {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bot-share-bar {
  flex: 1;
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-bg-tertiary);

  i {
    display: block;
    min-width: 3px;
    height: 100%;
    border-radius: inherit;
    background: var(--color-primary);
  }
}

.bot-share-value {
  flex: none;
  width: 36px;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: right;
}

.bot-detail {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 20px;
  padding: 4px 16px 16px 48px;
}

.bot-detail-col {
  min-width: 0;
}

.bot-detail-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.bot-detail-meta {
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 500;
}

.bot-detail-more {
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.usage-panel {
  margin-top: 20px;
}

.usage-table {
  font-variant-numeric: tabular-nums;
}

.usage-task {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.usage-models {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 16px 12px 48px;
}

.usage-model-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) 88px 92px 88px;
  align-items: center;
  gap: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.usage-model-name {
  overflow: hidden;
  color: var(--color-text);
  font-family: ui-monospace, 'JetBrains Mono', Monaco, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-model-provider {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-model-count,
.usage-model-rate,
.usage-model-time {
  text-align: right;
}

.dialog-search {
  margin-bottom: 12px;
}

.dialog-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: min(52vh, 420px);
  overflow-y: auto;
  padding-right: 4px;
}

@media (max-width: 1100px) {
  .stats-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .stats-view {
    padding: 16px;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }

  .bot-detail {
    grid-template-columns: minmax(0, 1fr);
    padding-left: 16px;
  }
}
</style>
