<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { dataApi, configApi } from '@/api'
import type { GroupRecord } from '@/types/api'

const loading = ref(false)
const groups = ref<GroupRecord[]>([])
const soonThresholdDays = ref(7)

const daysRemaining = (expiry: string): number => {
  try {
    const e = new Date(expiry)
    const n = new Date()
    e.setHours(0, 0, 0, 0)
    n.setHours(0, 0, 0, 0)
    return Math.round((e.getTime() - n.getTime()) / 86400000)
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

const activeGroups = computed(() => groups.value.length)
const validMembers = computed(() => groups.value.filter(g => g.status === 'active').length)
const expiringSoon = computed(() => groups.value.filter(g => g.status === 'soon' || g.status === 'today').length)
const expired = computed(() => groups.value.filter(g => g.status === 'expired').length)

const loadData = async () => {
  loading.value = true
  try {
    const [data, config] = await Promise.all([
      dataApi.getAll(),
      configApi.getAll().catch(() => ({}))
    ])

    const noticeDays = config?.membership?.expire_notice_days
    if (Array.isArray(noticeDays) && noticeDays.length) {
      soonThresholdDays.value = Math.max(...noticeDays.map(Number).filter(Number.isFinite))
    }

    groups.value = Object.entries(data)
      .filter(([k, v]) => k !== 'generatedCodes' && typeof v === 'object')
      .map(([gid, info]: [string, any]) => {
        const days = daysRemaining(info.expiry)
        return {
          gid,
          ...info,
          days,
          status: getStatus(days)
        }
      })
  } catch (e: any) {
    ElMessage.error('加载仪表盘失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div v-loading="loading" class="dashboard-view">
    <h2 class="page-title">仪表盘总览</h2>

    <div class="stats-cards-compact">
      <div class="stat-card-compact">
        <div class="stat-icon-compact">👥</div>
        <div class="stat-info-compact">
          <div class="stat-label-compact">活跃群组</div>
          <div class="stat-value-compact">{{ activeGroups }}</div>
        </div>
      </div>

      <div class="stat-card-compact success">
        <div class="stat-icon-compact">💎</div>
        <div class="stat-info-compact">
          <div class="stat-label-compact">有效会员</div>
          <div class="stat-value-compact">{{ validMembers }}</div>
        </div>
      </div>

      <div class="stat-card-compact warning">
        <div class="stat-icon-compact">⚠️</div>
        <div class="stat-info-compact">
          <div class="stat-label-compact">即将到期</div>
          <div class="stat-value-compact">{{ expiringSoon }}</div>
        </div>
      </div>

      <div class="stat-card-compact danger">
        <div class="stat-icon-compact">⏰</div>
        <div class="stat-info-compact">
          <div class="stat-label-compact">已到期</div>
          <div class="stat-value-compact">{{ expired }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.dashboard-view {
  padding: 24px;
}

.stats-cards-compact {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card-compact {
  background: var(--color-bg-card);
  backdrop-filter: blur(10px);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  transition: var(--transition);
  cursor: pointer;

  &:hover {
    transform: translateY(-4px);
    box-shadow: var(--shadow-lg);
    border-color: var(--color-primary);
  }

  .stat-icon-compact {
    font-size: 32px;
    width: 56px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-bg-tertiary);
    border-radius: var(--radius-md);
    flex-shrink: 0;
  }

  .stat-info-compact {
    flex: 1;
    min-width: 0;
  }

  .stat-label-compact {
    font-size: 13px;
    color: var(--color-text-secondary);
    margin-bottom: 6px;
    font-weight: 500;
  }

  .stat-value-compact {
    font-size: 28px;
    font-weight: 800;
    color: var(--color-text);
  }

  &.success .stat-value-compact {
    color: var(--color-success);
  }

  &.warning .stat-value-compact {
    color: var(--color-warning);
  }

  &.danger .stat-value-compact {
    color: var(--color-danger);
  }
}
</style>
