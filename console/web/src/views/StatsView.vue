<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { statsApi } from '@/api'
import type { StatsToday, BotStats } from '@/types/api'

const loading = ref(false)
const stats = ref<StatsToday>({ bots: {} })
const expandedBots = ref<string[]>([])

const totalSent = computed(() => {
  return Object.values(stats.value.bots).reduce((sum, b) => sum + b.total_sent, 0)
})

const totalBots = computed(() => Object.keys(stats.value.bots).length)

const totalGroups = computed(() => {
  return Object.values(stats.value.bots).reduce((sum, b) => sum + (b.group?.count || 0), 0)
})

const totalPrivate = computed(() => {
  return Object.values(stats.value.bots).reduce((sum, b) => sum + (b.private?.count || 0), 0)
})

const groupCount = computed(() => {
  const allGroups = new Set<string>()
  Object.values(stats.value.bots).forEach(b => {
    if (b.group?.targets) {
      Object.keys(b.group.targets).forEach(g => allGroups.add(g))
    }
  })
  return allGroups.size
})

const privateCount = computed(() => {
  const allUsers = new Set<string>()
  Object.values(stats.value.bots).forEach(b => {
    if (b.private?.targets) {
      Object.keys(b.private.targets).forEach(u => allUsers.add(u))
    }
  })
  return allUsers.size
})

const loadStats = async () => {
  loading.value = true
  try {
    stats.value = await statsApi.getToday()
  } catch (e: any) {
    ElMessage.error('加载统计失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

const toggleBot = (botId: string) => {
  const idx = expandedBots.value.indexOf(botId)
  if (idx >= 0) {
    expandedBots.value.splice(idx, 1)
  } else {
    expandedBots.value.push(botId)
  }
}

const isExpanded = (botId: string) => expandedBots.value.includes(botId)

const sortedTargets = (targets: Record<string, number> | undefined) => {
  if (!targets) return []
  return Object.entries(targets)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div v-loading="loading" class="stats-view">
    <div class="stats-overview-compact">
      <div class="stat-box-compact">
        <div class="stat-box-icon">📨</div>
        <div class="stat-box-info">
          <div class="stat-box-label">今日总消息</div>
          <div class="stat-box-value">{{ totalSent }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <div class="stat-box-icon">👥</div>
        <div class="stat-box-info">
          <div class="stat-box-label">群聊消息</div>
          <div class="stat-box-value">{{ totalGroups }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <div class="stat-box-icon">💬</div>
        <div class="stat-box-info">
          <div class="stat-box-label">私聊消息</div>
          <div class="stat-box-value">{{ totalPrivate }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <div class="stat-box-icon">👥</div>
        <div class="stat-box-info">
          <div class="stat-box-label">群聊数</div>
          <div class="stat-box-value">{{ groupCount }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <div class="stat-box-icon">💬</div>
        <div class="stat-box-info">
          <div class="stat-box-label">私聊人数</div>
          <div class="stat-box-value">{{ privateCount }}</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">Bot明细数据</h3>
      </div>
      <div class="stats-accordion">
        <div v-if="!totalBots" class="empty-state">暂无数据</div>

        <div
          v-for="(botStats, botId) in stats.bots"
          :key="botId"
          class="stats-bot-item"
        >
          <div
            class="stats-bot-header"
            :class="{ active: isExpanded(botId) }"
            @click="toggleBot(botId)"
          >
            <div class="stats-bot-title">
              <span class="stats-bot-icon">▶</span>
              <span>Bot {{ botId }}</span>
            </div>
            <div class="stats-bot-summary">
              <span>总计: <strong>{{ botStats.total_sent }}</strong></span>
              <span v-if="botStats.group">群聊: <strong>{{ botStats.group.count }}</strong></span>
              <span v-if="botStats.private">私聊: <strong>{{ botStats.private.count }}</strong></span>
            </div>
          </div>

          <div
            class="stats-bot-content"
            :class="{ active: isExpanded(botId) }"
          >
            <div class="stats-bot-body">
              <div class="stats-targets-grid">
                <div v-if="botStats.group" class="stats-target-section">
                  <div class="stats-target-title">👥 群聊消息</div>
                  <div class="stats-target-list">
                    <div
                      v-for="[tid, cnt] in sortedTargets(botStats.group.targets)"
                      :key="tid"
                      class="stats-target-item"
                    >
                      <span class="id">{{ tid }}</span>
                      <span class="count">{{ cnt }}</span>
                    </div>
                    <div v-if="!sortedTargets(botStats.group.targets).length" class="empty-state-mini">
                      暂无数据
                    </div>
                  </div>
                </div>

                <div v-if="botStats.private" class="stats-target-section">
                  <div class="stats-target-title">💬 私聊消息</div>
                  <div class="stats-target-list">
                    <div
                      v-for="[tid, cnt] in sortedTargets(botStats.private.targets)"
                      :key="tid"
                      class="stats-target-item"
                    >
                      <span class="id">{{ tid }}</span>
                      <span class="count">{{ cnt }}</span>
                    </div>
                    <div v-if="!sortedTargets(botStats.private.targets).length" class="empty-state-mini">
                      暂无数据
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.stats-view {
  padding: 24px;
}

.stats-overview-compact {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.stat-box-compact {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: var(--transition);

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }
}

.stat-box-icon {
  font-size: 28px;
  flex-shrink: 0;
}

.stat-box-info {
  flex: 1;
  min-width: 0;
}

.stat-box-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}

.stat-box-value {
  font-size: 24px;
  font-weight: 800;
  background: linear-gradient(135deg, var(--color-primary), var(--color-info));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.panel {
  background: var(--color-bg-card);
  backdrop-filter: blur(10px);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-sm);
  transition: var(--transition);

  &:hover {
    box-shadow: var(--shadow-md);
  }
}

.panel-header {
  margin-bottom: 20px;
}

.panel-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
}

.stats-accordion {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.stats-bot-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  overflow: hidden;
  transition: var(--transition);

  &:hover {
    box-shadow: var(--shadow-sm);
  }
}

.stats-bot-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: var(--color-bg-tertiary);
  cursor: pointer;
  transition: var(--transition);
  user-select: none;

  &:hover {
    background: var(--color-border-light);
  }

  &.active {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(6, 182, 212, 0.1));
    border-bottom: 1px solid var(--color-border);
  }
}

.stats-bot-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 700;
  font-size: 15px;
  color: var(--color-text);
}

.stats-bot-icon {
  font-size: 18px;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.stats-bot-header.active .stats-bot-icon {
  transform: rotate(90deg);
}

.stats-bot-summary {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: var(--color-text-secondary);

  strong {
    font-weight: 600;
  }
}

.stats-bot-content {
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1);

  &.active {
    max-height: 1000px;
  }
}

.stats-bot-body {
  padding: 16px;
}

.stats-targets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.stats-target-section {
  background: var(--color-bg-tertiary);
  border-radius: var(--radius-md);
  padding: 14px;
}

.stats-target-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.stats-target-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.stats-target-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--color-text-secondary);
  padding: 4px 0;

  .id {
    font-family: 'Courier New', monospace;
    font-weight: 600;
  }

  .count {
    background: var(--color-primary);
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 700;
  }
}

.empty-state {
  padding: 60px 20px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.empty-state-mini {
  padding: 12px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
</style>
