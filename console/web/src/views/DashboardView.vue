<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Clock, GoldMedal, Refresh, User, Warning } from '@element-plus/icons-vue'
import MetricCard from '@/components/MetricCard.vue'
import TrendChart from '@/components/TrendChart.vue'
import { chatApi, configApi, dataApi, metaApi, renewalApi, statsApi, systemApi } from '@/api'
import type {
  ChatHealth,
  GroupRecord,
  StatsHistoryDay,
  StatsToday,
  SystemStatus,
} from '@/types/api'

const emit = defineEmits<{ navigate: [page: string] }>()

const loading = ref(false)
const groups = ref<GroupRecord[]>([])
const soonThresholdDays = ref(7)

const health = ref<ChatHealth | null>(null)
const system = ref<SystemStatus | null>(null)
const history = ref<StatsHistoryDay[]>([])
const botsOnline = ref(0)

const todaySent = ref(0)
const todayGroup = ref(0)
const todayPrivate = ref(0)

const now = new Date()
const greeting = computed(() => {
  const hour = now.getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
})
const dateText = `${now.getFullYear()} 年 ${now.getMonth() + 1} 月 ${now.getDate()} 日`
const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][now.getDay()]

const daysRemaining = (expiry: string): number => {
  try {
    const end = new Date(expiry)
    const start = new Date()
    end.setHours(0, 0, 0, 0)
    start.setHours(0, 0, 0, 0)
    return Math.round((end.getTime() - start.getTime()) / 86400000)
  } catch {
    return 0
  }
}

const getStatus = (days: number): GroupRecord['status'] => {
  if (days < 0) return 'expired'
  if (days === 0) return 'today'
  if (days <= soonThresholdDays.value) return 'soon'
  return 'active'
}

const totalGroups = computed(() => groups.value.length)
const validMembers = computed(() => groups.value.filter((group) => group.status === 'active').length)
const expiringSoon = computed(
  () => groups.value.filter((group) => group.status === 'soon' || group.status === 'today').length,
)
const expired = computed(() => groups.value.filter((group) => group.status === 'expired').length)
const validRate = computed(() =>
  totalGroups.value ? Math.round((validMembers.value / totalGroups.value) * 100) : 0,
)

const heroSub = computed(() => {
  if (!totalGroups.value) return '还没有登记任何会员群，先去会员续费页添加。'
  if (!expiringSoon.value && !expired.value) return '所有群的会员状态都正常。'
  const parts: string[] = []
  if (expiringSoon.value) parts.push(`${expiringSoon.value} 个群即将到期`)
  if (expired.value) parts.push(`${expired.value} 个群已过期`)
  return `今天需要关注：${parts.join('，')}。`
})

const donutStyle = computed(() => {
  if (!totalGroups.value) return {}
  const valid = (validMembers.value / totalGroups.value) * 100
  const soon = (expiringSoon.value / totalGroups.value) * 100
  return {
    background: `conic-gradient(var(--color-success) 0 ${valid}%, var(--color-warning) ${valid}% ${
      valid + soon
    }%, var(--color-danger) ${valid + soon}% 100%)`,
  }
})

const upcomingGroups = computed(() =>
  groups.value
    .filter((group) => group.status !== 'active')
    .sort((a, b) => a.days - b.days)
    .slice(0, 6),
)

const trendItems = computed(() => history.value.slice(-14))

const cpu = computed(() => system.value?.cpu)
const memory = computed(() => system.value?.memory)
const systemDisk = computed(() => system.value?.disks?.[0])
const network = computed(() => system.value?.network)
const processInfo = computed(() => system.value?.process)
const systemInfo = computed(() => system.value?.system)

const formatExpiry = (expiry: string) => {
  if (!expiry) return '-'
  try {
    const date = new Date(expiry)
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    return `${date.getFullYear()}/${month}/${day}`
  } catch {
    return expiry
  }
}

const statusLabel = (status: GroupRecord['status']) => {
  switch (status) {
    case 'active':
      return '有效'
    case 'soon':
      return '即将到期'
    case 'today':
      return '今日到期'
    case 'expired':
      return '已过期'
    default:
      return status
  }
}

const remainingText = (group: GroupRecord) => {
  if (group.status === 'expired') return `已过期 ${Math.abs(group.days)} 天`
  if (group.status === 'today') return '今天到期'
  return `剩余 ${group.days} 天`
}

const formatNumber = (value: number) => value.toLocaleString('zh-CN')

const formatBytes = (value?: number) => {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)))
  const size = value / 1024 ** index
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
}

const formatSpeed = (value?: number) => `${formatBytes(value)}/s`

const formatDuration = (seconds?: number) => {
  if (!seconds) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days} 天 ${hours} 小时`
  if (hours) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分钟`
}

const gaugeTone = (percent?: number | null) => {
  if (percent == null) return ''
  if (percent >= 90) return 'is-danger'
  if (percent >= 75) return 'is-warning'
  return ''
}

const sparkPath = (values?: number[], height = 24) => {
  if (!values || values.length < 2) return ''
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100
      const y = height - (Math.min(Math.max(value, 0), 100) / 100) * height
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

const getApiErrorMessage = (error: any): string => {
  const detail = error?.response?.data?.detail
  if (detail) return detail
  if (error?.response?.status === 404) return '群不存在或不在会员库'
  if (error?.response?.status === 409) return '所有在线 Bot 都无法发送消息'
  return error?.message || String(error)
}

const renewDialog = ref(false)
const renewTarget = ref<GroupRecord | null>(null)
const renewForm = ref({ length: 30, unit: '天' })
const renewing = ref(false)

const openRenew = (group: GroupRecord) => {
  renewTarget.value = group
  renewForm.value = { length: 30, unit: '天' }
  renewDialog.value = true
}

const submitRenew = async () => {
  if (!renewTarget.value) return
  renewing.value = true
  try {
    await renewalApi.extend({
      group_id: renewTarget.value.gid,
      length: renewForm.value.length,
      unit: renewForm.value.unit,
      renewed_by: '控制台',
      remark: renewTarget.value.remark,
    })
    ElMessage.success('续费成功')
    renewDialog.value = false
    await loadData()
  } catch (error: any) {
    ElMessage.error('续费失败: ' + getApiErrorMessage(error))
  } finally {
    renewing.value = false
  }
}

const remind = async (group: GroupRecord) => {
  const groupId = Number(group.gid)
  if (!Number.isFinite(groupId)) {
    ElMessage.warning('该群号不是数字，无法发送提醒')
    return
  }
  try {
    await ElMessageBox.confirm(`向群 ${group.gid} 发送到期提醒？`, '发送提醒', {
      type: 'warning',
      confirmButtonText: '发送',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const result = await renewalApi.remind(groupId)
    ElMessage.success(`提醒已发送（${result.sent} 条）`)
  } catch (error: any) {
    ElMessage.error('发送失败: ' + getApiErrorMessage(error))
  }
}

const loadData = async () => {
  loading.value = true
  try {
    const [data, config, today, historyData, healthData, systemData, botsData] = await Promise.all([
      dataApi.getAll(),
      configApi.getAll().catch(() => null),
      statsApi.getToday().catch((): StatsToday => ({ bots: {} })),
      statsApi.getHistory(14).catch(() => ({ days: 0, items: [] as StatsHistoryDay[] })),
      chatApi.getHealth().catch(() => null),
      systemApi.status().catch(() => null),
      metaApi.getBots().catch(() => ({ bots: [] })),
    ])

    const noticeDays = Number(config?.membership?.expire_notice_days)
    if (Number.isFinite(noticeDays)) {
      soonThresholdDays.value = noticeDays
    }

    groups.value = Object.entries(data)
      .filter(([key, value]) => key !== 'generatedCodes' && typeof value === 'object')
      .map(([gid, info]: [string, any]) => {
        const days = daysRemaining(info.expiry)
        return {
          id: info.id,
          gid,
          expiry: info.expiry,
          days,
          status: getStatus(days),
          managed_by_bot: info.managed_by_bot,
          last_renewed_by: info.last_renewed_by,
          remark: info.remark,
        }
      })

    const botStats = Object.values(today.bots || {})
    todaySent.value = botStats.reduce((total, bot) => total + bot.total_sent, 0)
    todayGroup.value = botStats.reduce((total, bot) => total + (bot.group?.count || 0), 0)
    todayPrivate.value = botStats.reduce((total, bot) => total + (bot.private?.count || 0), 0)

    history.value = historyData.items || []
    health.value = healthData
    system.value = systemData
    botsOnline.value = botsData.bots?.length || 0
  } catch (error: any) {
    ElMessage.error('加载仪表盘失败: ' + getApiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="dashboard-view">
    <section class="dash-hero">
      <div class="hero-main">
        <div class="hero-eyebrow">{{ dateText }} · {{ weekday }}</div>
        <h1 class="hero-title">{{ greeting }}，FxBot</h1>
        <p class="hero-sub">{{ heroSub }}</p>
        <div v-if="health" class="hero-chips">
          <span class="chip">
            <i class="chip-dot" />
            {{ botsOnline || health.online_bots }} 个 Bot 在线
          </span>
          <span class="chip" :class="{ 'is-muted': !health.enabled }">
            AI 对话{{ health.enabled ? '已启用' : '未启用' }}
          </span>
          <span class="chip" :class="health.storage_ready ? '' : 'is-warning'">
            聊天存储{{ health.storage_ready ? '正常' : '降级' }}
          </span>
        </div>
      </div>
      <div class="hero-actions">
        <el-button :icon="Refresh" :loading="loading" @click="loadData">刷新</el-button>
      </div>
    </section>

    <div class="metric-grid">
      <MetricCard
        label="群组总数"
        :value="formatNumber(totalGroups)"
        :hint="`${validMembers} 个有效会员`"
        :icon="User"
      />
      <MetricCard
        label="有效会员"
        :value="formatNumber(validMembers)"
        :hint="`总群占比 ${validRate}%`"
        :icon="GoldMedal"
        tone="success"
      />
      <MetricCard
        label="即将到期"
        :value="formatNumber(expiringSoon)"
        hint="含今日到期"
        :icon="Warning"
        tone="warning"
      />
      <MetricCard
        label="已过期"
        :value="formatNumber(expired)"
        hint="需要尽快处理"
        :icon="Clock"
        tone="danger"
      />
    </div>

    <div class="dash-grid">
      <section class="panel">
        <div class="panel-header">
          <h3 class="panel-title">会员分布</h3>
          <span class="panel-sub">共 {{ totalGroups }} 个群</span>
        </div>
        <div class="donut-wrap">
          <div class="donut" :style="donutStyle">
            <div class="donut-hole">
              <span class="donut-value">{{ validRate }}%</span>
              <span class="donut-label">有效率</span>
            </div>
          </div>
          <div class="donut-legend">
            <div class="legend-row">
              <i class="legend-dot is-success" />
              <span>有效</span>
              <strong>{{ validMembers }}</strong>
            </div>
            <div class="legend-row">
              <i class="legend-dot is-warning" />
              <span>即将到期</span>
              <strong>{{ expiringSoon }}</strong>
            </div>
            <div class="legend-row">
              <i class="legend-dot is-danger" />
              <span>已过期</span>
              <strong>{{ expired }}</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div class="panel-heading">
            <h3 class="panel-title">消息趋势</h3>
            <p class="panel-sub">近 14 天发送量</p>
          </div>
          <button class="link-btn" type="button" @click="emit('navigate', 'stats')">
            消息统计
            <el-icon><ArrowRight /></el-icon>
          </button>
        </div>
        <div class="trend-meta">
          <div class="trend-today">
            <span class="trend-value">{{ formatNumber(todaySent) }}</span>
            <span class="trend-unit">今日发送</span>
          </div>
          <div class="trend-split">群聊 {{ formatNumber(todayGroup) }} · 私聊 {{ formatNumber(todayPrivate) }}</div>
        </div>
        <TrendChart :items="trendItems" :height="148" :show-axis="false" />
      </section>

      <section class="panel system-panel">
        <div class="panel-header">
          <h3 class="panel-title">运行状态</h3>
          <span v-if="systemInfo" class="panel-sub">{{ systemInfo.platform }} {{ systemInfo.release }}</span>
        </div>

        <template v-if="system?.available">
          <div class="sys-row">
            <div class="sys-head">
              <span class="sys-label">CPU</span>
              <svg class="sys-spark" viewBox="0 0 100 24" preserveAspectRatio="none">
                <path
                  :d="sparkPath(cpu?.history)"
                  fill="none"
                  stroke="var(--color-primary)"
                  stroke-width="1.5"
                  vector-effect="non-scaling-stroke"
                />
              </svg>
              <span class="sys-value">{{ cpu?.percent ?? 0 }}%</span>
            </div>
            <div class="sys-bar">
              <i :class="gaugeTone(cpu?.percent)" :style="{ width: `${cpu?.percent ?? 0}%` }" />
            </div>
            <div class="sys-foot">
              {{ cpu?.count }} 核{{ cpu?.freq_mhz ? ` · ${(cpu.freq_mhz / 1000).toFixed(1)} GHz` : '' }}
            </div>
          </div>

          <div class="sys-row">
            <div class="sys-head">
              <span class="sys-label">内存</span>
              <svg class="sys-spark" viewBox="0 0 100 24" preserveAspectRatio="none">
                <path
                  :d="sparkPath(memory?.history)"
                  fill="none"
                  stroke="var(--color-info)"
                  stroke-width="1.5"
                  vector-effect="non-scaling-stroke"
                />
              </svg>
              <span class="sys-value">{{ memory?.percent ?? 0 }}%</span>
            </div>
            <div class="sys-bar">
              <i :class="gaugeTone(memory?.percent)" :style="{ width: `${memory?.percent ?? 0}%` }" />
            </div>
            <div class="sys-foot">
              {{ formatBytes(memory?.used) }} / {{ formatBytes(memory?.total) }}
            </div>
          </div>

          <div class="sys-row">
            <div class="sys-head">
              <span class="sys-label">磁盘 {{ systemDisk?.mount || '' }}</span>
              <span class="sys-value">{{ systemDisk?.percent ?? 0 }}%</span>
            </div>
            <div class="sys-bar">
              <i :class="gaugeTone(systemDisk?.percent)" :style="{ width: `${systemDisk?.percent ?? 0}%` }" />
            </div>
            <div class="sys-foot">
              {{ formatBytes(systemDisk?.used) }} / {{ formatBytes(systemDisk?.total) }}
            </div>
          </div>

          <div class="sys-net">
            <span>↓ {{ formatSpeed(network?.download) }}</span>
            <span>↑ {{ formatSpeed(network?.upload) }}</span>
          </div>

          <div class="sys-footer">
            进程 {{ formatBytes(processInfo?.rss) }} · {{ processInfo?.threads ?? 0 }} 线程 ·
            开机 {{ formatDuration(systemInfo?.uptime) }}
          </div>
        </template>

        <div v-else class="sys-unavailable">无法读取系统状态</div>
      </section>
    </div>

    <section class="panel">
      <div class="panel-header">
        <div class="panel-heading">
          <h3 class="panel-title">待处理会员</h3>
          <p class="panel-sub">按到期时间排序，最多显示 6 个</p>
        </div>
        <button class="link-btn" type="button" @click="emit('navigate', 'membership')">
          全部会员
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>

      <div v-if="upcomingGroups.length" class="expire-list">
        <div v-for="group in upcomingGroups" :key="group.gid" class="expire-row">
          <div class="expire-main">
            <span class="expire-name">{{ group.remark || `群 ${group.gid}` }}</span>
            <span class="expire-gid">{{ group.gid }}</span>
          </div>
          <span class="status-pill" :class="`is-${group.status}`">{{ statusLabel(group.status) }}</span>
          <span class="expire-date">{{ formatExpiry(group.expiry) }}</span>
          <span class="expire-days">{{ remainingText(group) }}</span>
          <div class="expire-actions">
            <el-button size="small" @click="remind(group)">提醒</el-button>
            <el-button size="small" type="primary" @click="openRenew(group)">续费</el-button>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">没有需要处理的会员</div>
    </section>

    <el-dialog v-model="renewDialog" title="快速续费" width="380px">
      <div class="renew-target">
        <span>{{ renewTarget?.remark || '未知群组' }}</span>
        <code>{{ renewTarget?.gid }}</code>
      </div>
      <el-form label-width="72px">
        <el-form-item label="续费时长">
          <el-input-number v-model="renewForm.length" :min="1" :max="3650" controls-position="right" />
          <el-select v-model="renewForm.unit" class="renew-unit">
            <el-option label="天" value="天" />
            <el-option label="月" value="月" />
            <el-option label="年" value="年" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renewDialog = false">取消</el-button>
        <el-button type="primary" :loading="renewing" @click="submitRenew">确认续费</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.dashboard-view {
  padding: 24px 28px 32px;
}

.dash-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
  padding: 22px 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background:
    radial-gradient(circle at 100% 0%, var(--color-primary-soft) 0%, transparent 46%),
    var(--color-bg-card);
}

.hero-main {
  min-width: 0;
}

.hero-eyebrow {
  color: var(--color-text-tertiary);
  font-size: 12px;
  letter-spacing: 0.02em;
}

.hero-title {
  margin: 6px 0 0;
  color: var(--color-text);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.2;
}

.hero-sub {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.hero-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  font-size: 12px;

  &.is-muted {
    color: var(--color-text-tertiary);
  }

  &.is-warning {
    border-color: color-mix(in srgb, var(--color-warning) 40%, transparent);
    color: var(--color-warning);
  }
}

.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}

.hero-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.dash-grid {
  display: grid;
  grid-template-columns: minmax(260px, 0.9fr) minmax(300px, 1.15fr) minmax(300px, 1fr);
  gap: 20px;

  .panel {
    display: flex;
    flex-direction: column;
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

.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;

  &:hover {
    color: var(--color-primary-hover);
  }
}

.donut-wrap {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 24px;
  padding-top: 6px;
}

.donut {
  position: relative;
  flex: none;
  width: 124px;
  height: 124px;
  border-radius: 50%;
  background: var(--color-bg-tertiary);

  &::before {
    content: '';
    position: absolute;
    inset: 16px;
    border-radius: 50%;
    background: var(--color-bg-card);
  }
}

.donut-hole {
  position: absolute;
  inset: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}

.donut-value {
  color: var(--color-text);
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  line-height: 1;
}

.donut-label {
  margin-top: 4px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.donut-legend {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;

  strong {
    margin-left: auto;
    color: var(--color-text);
    font-variant-numeric: tabular-nums;
  }
}

.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;

  &.is-success {
    background: var(--color-success);
  }

  &.is-warning {
    background: var(--color-warning);
  }

  &.is-danger {
    background: var(--color-danger);
  }
}

.trend-meta {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.trend-today {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.trend-value {
  color: var(--color-text);
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  line-height: 1;
}

.trend-unit,
.trend-split {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.sys-row + .sys-row {
  margin-top: 12px;
}

.sys-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sys-label {
  flex: none;
  width: 64px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.sys-spark {
  flex: 1;
  min-width: 0;
  height: 24px;
  opacity: 0.9;
}

.sys-value {
  flex: none;
  color: var(--color-text);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.sys-bar {
  height: 5px;
  margin: 6px 0 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-bg-tertiary);

  i {
    display: block;
    min-width: 2px;
    height: 100%;
    border-radius: inherit;
    background: var(--color-primary);
    transition: width 0.3s ease;

    &.is-warning {
      background: var(--color-warning);
    }

    &.is-danger {
      background: var(--color-danger);
    }
  }
}

.sys-foot {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.sys-net {
  display: flex;
  gap: 16px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-light);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.sys-footer {
  margin-top: 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.sys-unavailable {
  padding: 24px 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.expire-list {
  display: flex;
  flex-direction: column;
}

.expire-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 2px;
  border-bottom: 1px solid var(--color-border-light);

  &:last-child {
    border-bottom: 0;
    padding-bottom: 2px;
  }

  &:first-child {
    padding-top: 2px;
  }
}

.expire-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.expire-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.expire-gid {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-family: ui-monospace, 'JetBrains Mono', Monaco, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-pill {
  flex: none;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;

  &.is-soon {
    background: color-mix(in srgb, var(--color-warning) 14%, transparent);
    color: var(--color-warning);
  }

  &.is-today {
    background: color-mix(in srgb, var(--color-warning) 20%, transparent);
    color: var(--color-warning);
  }

  &.is-expired {
    background: color-mix(in srgb, var(--color-danger) 14%, transparent);
    color: var(--color-danger);
  }

  &.is-active {
    background: color-mix(in srgb, var(--color-success) 14%, transparent);
    color: var(--color-success);
  }
}

.expire-date,
.expire-days {
  flex: none;
  width: 92px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.expire-days {
  width: 96px;
  color: var(--color-text-tertiary);
}

.expire-actions {
  display: flex;
  flex: none;
  gap: 6px;
}

.renew-target {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 13px;

  code {
    color: var(--color-text-tertiary);
    font-size: 11px;
    word-break: break-all;
  }
}

.renew-unit {
  width: 84px;
  margin-left: 8px;
}

@media (max-width: 1180px) {
  .dash-grid {
    grid-template-columns: minmax(260px, 1fr) minmax(300px, 1.2fr);

    .system-panel {
      grid-column: 1 / -1;
    }
  }
}

@media (max-width: 860px) {
  .dashboard-view {
    padding: 16px;
  }

  .dash-hero {
    flex-direction: column;
  }

  .dash-grid {
    grid-template-columns: minmax(0, 1fr);

    .system-panel {
      grid-column: auto;
    }
  }

  .expire-date {
    display: none;
  }
}

@media (max-width: 720px) {
  .hero-title {
    font-size: 22px;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }

  .expire-gid {
    display: none;
  }

  .expire-days {
    display: none;
  }
}
</style>
