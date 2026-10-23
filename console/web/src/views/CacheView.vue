<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Coin, Cpu, Collection, Refresh } from '@element-plus/icons-vue'
import { cacheApi } from '@/api'
import type { CacheStats, CacheKeys, CacheClearPayload } from '@/types/api'

const loading = ref(false)
const clearing = ref(false)
const stats = ref<CacheStats | null>(null)
const keysVisible = ref(false)
const keysLoading = ref(false)
const selectedDomain = ref('')
const keys = ref<CacheKeys | null>(null)
let keysRequest = 0

const formatBytes = (bytes: number | null | undefined) => {
  if (bytes == null) return '暂无数据'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = Math.max(0, bytes)
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex++
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 2)} ${units[unitIndex]}`
}

const formatTime = (timestamp: number) => new Date(timestamp * 1000).toLocaleString()

const formatTtl = (ttl: number | null) => {
  if (ttl === null) return '永久'
  const remaining = Math.max(0, ttl)
  return remaining < 60 ? `剩余 ${Math.ceil(remaining)}s` : `剩余 ${(remaining / 60).toFixed(1)}m`
}

const loadKeys = async () => {
  const request = ++keysRequest
  const domain = selectedDomain.value
  keysLoading.value = true
  keys.value = null
  try {
    const result = await cacheApi.keys(domain)
    if (request === keysRequest) keys.value = result
  } catch (error) {
    console.error('加载缓存键失败', error)
    if (request === keysRequest) ElMessage.error('加载缓存键失败，请重试')
  } finally {
    if (request === keysRequest) keysLoading.value = false
  }
}

const loadStats = async () => {
  try {
    stats.value = await cacheApi.stats()
  } catch (error) {
    console.error('加载缓存统计失败', error)
    ElMessage.error('加载缓存统计失败，请重试')
  }
}

const refresh = async () => {
  if (loading.value) return
  loading.value = true
  try {
    await Promise.all([loadStats(), ...(keysVisible.value ? [loadKeys()] : [])])
  } finally {
    loading.value = false
  }
}

const showKeys = (domain: string) => {
  selectedDomain.value = domain
  keysVisible.value = true
  void loadKeys()
}

const clearCache = async (payload: CacheClearPayload) => {
  if (clearing.value || loading.value || keysLoading.value) return
  clearing.value = true
  try {
    const target = payload.scope === 'all'
      ? '全部缓存'
      : payload.scope === 'domain' ? `缓存域「${payload.domain}」` : `缓存键「${payload.key}」`
    try {
      await ElMessageBox.confirm(`确定清空${target}？此操作不可撤销。`, '清空缓存', {
        confirmButtonText: '确定清空',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch (action) {
      if (action === 'cancel' || action === 'close') return
      throw action
    }
    const result = await cacheApi.clear(payload)
    ElMessage.success(result.removed === null ? '缓存已清空' : `缓存已清空，移除 ${result.removed} 个条目`)
    await refresh()
  } catch (error) {
    console.error('清空缓存失败', error)
    ElMessage.error('清空缓存失败，请重试')
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="cache-view">
    <div class="page-header">
      <h2>缓存</h2>
      <div class="page-actions">
        <el-button :icon="Refresh" :loading="loading" :disabled="clearing" @click="refresh">刷新</el-button>
        <el-button type="danger" :loading="clearing" :disabled="loading" @click="clearCache({ scope: 'all' })">
          清空全部
        </el-button>
      </div>
    </div>

    <div class="stats-overview-compact">
      <div class="stat-box-compact">
        <el-icon class="stat-box-icon"><Cpu /></el-icon>
        <div>
          <div class="stat-box-label">进程 RSS</div>
          <div class="stat-box-value">{{ formatBytes(stats?.process.rss) }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <el-icon class="stat-box-icon"><Coin /></el-icon>
        <div>
          <div class="stat-box-label">缓存总字节</div>
          <div class="stat-box-value">{{ formatBytes(stats?.process.cache_bytes) }}</div>
        </div>
      </div>
      <div class="stat-box-compact">
        <el-icon class="stat-box-icon"><Collection /></el-icon>
        <div>
          <div class="stat-box-label">条目数</div>
          <div class="stat-box-value">{{ stats?.process.entries ?? '暂无数据' }}</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3 class="panel-title">缓存域明细</h3>
      <el-table v-loading="loading" :data="stats?.domains ?? []" row-key="domain" empty-text="暂无缓存域">
        <el-table-column prop="domain" label="缓存域" min-width="180" show-overflow-tooltip />
        <el-table-column prop="entries" label="条目数" width="90" />
        <el-table-column label="占用" min-width="110">
          <template #default="{ row }">{{ formatBytes(row.bytes) }}</template>
        </el-table-column>
        <el-table-column label="命中率" width="100">
          <template #default="{ row }">{{ (row.hit_rate * 100).toFixed(2) }}%</template>
        </el-table-column>
        <el-table-column label="命中/未命中" min-width="130">
          <template #default="{ row }">{{ row.hits }} / {{ row.misses }}</template>
        </el-table-column>
        <el-table-column label="淘汰/过期" min-width="120">
          <template #default="{ row }">{{ row.evictions }} / {{ row.expired }}</template>
        </el-table-column>
        <el-table-column prop="sets" label="写入次数" width="100" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" :disabled="clearing || loading" @click="clearCache({ scope: 'domain', domain: row.domain })">
              清空该域
            </el-button>
            <el-button link type="primary" :disabled="clearing || loading" @click="showKeys(row.domain)">查看键</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="keysVisible" :title="`缓存键 · ${selectedDomain}`" width="90%">
      <div class="keys-header">
        <span v-if="keys">共 {{ keys.total }} 个键，当前显示 {{ keys.keys.length }} 个（最多 200 个）</span>
        <span v-else>{{ keysLoading ? '加载中…' : '加载失败，请刷新重试' }}</span>
        <el-button :icon="Refresh" :loading="keysLoading" :disabled="clearing || loading" @click="loadKeys">刷新键列表</el-button>
      </div>
      <el-table v-loading="keysLoading" :data="keys?.keys ?? []" row-key="key" empty-text="暂无缓存键" max-height="500">
        <el-table-column prop="key" label="键名" min-width="240" show-overflow-tooltip />
        <el-table-column label="占用" min-width="110">
          <template #default="{ row }">{{ formatBytes(row.bytes) }}</template>
        </el-table-column>
        <el-table-column label="TTL" min-width="120">
          <template #default="{ row }">{{ formatTtl(row.ttl) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="最后访问时间" min-width="180">
          <template #default="{ row }">{{ formatTime(row.accessed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" :disabled="clearing || loading || keysLoading" @click="clearCache({ scope: 'key', key: row.key })">
              清空此键
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.cache-view {
  padding: 24px;
}

.page-header,
.keys-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  color: var(--color-text);
}

.stats-overview-compact {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.stat-box-compact {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px;
  transition: var(--transition);

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }
}

.stat-box-icon {
  font-size: 28px;
  color: var(--color-primary);
  flex-shrink: 0;
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
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-sm);
}

.panel-title {
  margin: 0 0 20px;
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
}

.keys-header {
  color: var(--color-text-secondary);
}
</style>
