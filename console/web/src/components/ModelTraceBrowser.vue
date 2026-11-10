<script setup lang="ts">
// 浏览交互参考 MaiBot dashboard 的 reasoning-process 页面，数据读取沿用 FxBot 会话边界。
import { computed, ref, watch } from 'vue'
import { Clock, Search } from '@element-plus/icons-vue'
import { chatApi } from '@/api'
import type { ChatModelTrace } from '@/types/api'
import { filterModelTraces, traceDuration, traceStage, type TraceRecord } from '@/utils/modelTrace'
import ModelTraceDetail from './ModelTraceDetail.vue'

const props = defineProps<{ tenant: string; items: ChatModelTrace[]; loading: boolean }>()
const query = ref('')
const stage = ref('')
const status = ref('')
const page = ref(1)
const selectedPath = ref('')
const detail = ref<TraceRecord | null>(null)
const detailLoading = ref(false)
const detailError = ref(false)
const PAGE_SIZE = 15
let detailRequest = 0

const stages = computed(() => [...new Set(props.items.map((item) => item.task || item.category))])
const filtered = computed(() => filterModelTraces(props.items, query.value, stage.value, status.value))
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const currentPage = computed(() => Math.min(page.value, pageCount.value))
const rows = computed(() => filtered.value.slice((currentPage.value - 1) * PAGE_SIZE, currentPage.value * PAGE_SIZE))
const selected = computed(() => rows.value.find((item) => item.path === selectedPath.value) || rows.value[0] || null)

const recordTitle = (item: ChatModelTrace) => item.reply_preview || item.reasoning_preview || '本次调用没有可见文字'
const timeLabel = (value: string) => {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

const loadDetail = async () => {
  const request = ++detailRequest
  const path = selected.value?.path
  const tenant = props.tenant
  detail.value = null
  detailError.value = false
  detailLoading.value = !!path && !!tenant
  if (!path || !tenant) return
  try {
    const result = await chatApi.getModelTrace(path)
    if (request === detailRequest && tenant === props.tenant && path === selected.value?.path) {
      detail.value = result.snapshot
    }
  } catch (error) {
    if (request === detailRequest && tenant === props.tenant && path === selected.value?.path) {
      console.error('读取思考过程失败', error)
      detailError.value = true
    }
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

watch([query, stage, status, () => props.tenant], () => {
  page.value = 1
  selectedPath.value = ''
})
watch(() => [props.tenant, selected.value?.path, selected.value?.created_at, selected.value?.size], loadDetail, { immediate: true })
</script>

<template>
  <section class="reasoning-workspace" aria-label="思考过程浏览器">
    <div class="reasoning-toolbar">
      <div><h3>调用记录</h3><p>选择一次调用，阅读输出、思考与完整上下文。</p></div>
    </div>
    <div class="reasoning-layout">
      <aside class="record-sidebar">
        <div class="record-filters">
          <el-input v-model="query" :prefix-icon="Search" placeholder="搜索回复、思考或模型…" aria-label="搜索思考记录" clearable />
          <div class="filter-row">
            <el-select v-model="stage" placeholder="全部阶段" aria-label="按调用阶段筛选">
              <el-option label="全部阶段" value="" />
              <el-option v-for="value in stages" :key="value" :value="value" :label="traceStage(value)" />
            </el-select>
            <el-select v-model="status" placeholder="全部状态" aria-label="按调用状态筛选">
              <el-option label="全部状态" value="" />
              <el-option label="完成" value="completed" />
              <el-option label="失败" value="failed" />
            </el-select>
          </div>
        </div>
        <nav class="record-list" aria-label="模型调用记录" :aria-busy="loading">
          <button
            v-for="item in rows" :key="item.path" type="button" class="record-card"
            :class="{ selected: item.path === selected?.path }" :aria-pressed="item.path === selected?.path"
            @click="selectedPath = item.path"
          >
            <div class="record-topline"><span class="record-stage">{{ traceStage(item.task || item.category) }}</span><span class="record-result" :class="{ failed: item.status === 'failed' }">{{ item.status === 'failed' ? '失败' : item.status === 'completed' ? '完成' : '未知' }}</span></div>
            <p class="record-title">{{ recordTitle(item) }}</p>
            <div class="record-model" :title="item.output_title">{{ item.model_name || item.provider || '未知模型' }}</div>
            <div class="record-foot"><time>{{ timeLabel(item.created_at) }}</time><span><el-icon><Clock /></el-icon>{{ traceDuration(item.duration_ms) }}</span></div>
          </button>
          <div v-if="!rows.length" class="records-empty">{{ loading ? '正在读取记录…' : items.length ? '没有匹配的记录，试试其他筛选条件。' : '此会话暂无模型记录。启用模型快照后，新调用会出现在这里。' }}</div>
        </nav>
        <footer class="record-pager">
          <button type="button" aria-label="上一页记录" :disabled="currentPage <= 1" @click="page = currentPage - 1">‹</button>
          <span>{{ filtered.length }} 条 · {{ currentPage }} / {{ pageCount }} 页</span>
          <button type="button" aria-label="下一页记录" :disabled="currentPage >= pageCount" @click="page = currentPage + 1">›</button>
        </footer>
      </aside>
      <section class="record-reader" :aria-busy="detailLoading" aria-label="当前思考记录详情">
        <div v-if="detailLoading" class="reader-placeholder" role="status"><div class="loading-line" /><div class="loading-line short" /><p>正在读取完整思考过程…</p></div>
        <div v-else-if="detailError" class="reader-placeholder" role="alert"><strong>这条记录暂时无法读取</strong><p>记录可能已被清理，刷新列表或重新加载后再试。</p><el-button @click="loadDetail">重新加载</el-button></div>
        <ModelTraceDetail v-else-if="detail" :snapshot="detail" />
        <div v-else class="reader-placeholder"><el-icon class="reader-empty-icon"><Search /></el-icon><strong>每一次思考，都有迹可循</strong><p>选择左侧记录，在这里阅读输出、思考过程和完整调用链。</p></div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.reasoning-workspace {
  min-width: 0;
}

.reasoning-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.reasoning-toolbar h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 15px;
  font-weight: 600;
}

.reasoning-toolbar p {
  margin: 5px 0 0;
  color: var(--color-text-tertiary);
  font-size: 11.5px;
}

.reasoning-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  min-height: 650px;
  height: min(900px, 80vh);
  gap: 18px;
}

.record-sidebar {
  display: flex;
  min-height: 0;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg-secondary);
  overflow: hidden;
}

.record-filters {
  padding: 14px;
  border-bottom: 1px solid var(--color-border-light);
}

.filter-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 10px;
}

.record-list {
  flex: 1;
  min-height: 0;
  padding: 8px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.record-card {
  display: block;
  width: 100%;
  margin-bottom: 4px;
  padding: 14px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.record-card:hover {
  background: var(--color-bg-card);
  border-color: var(--color-border-light);
}

.record-card.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.record-card:focus-visible, .record-pager button:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -2px;
}

.record-topline {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10px;
}

.record-stage {
  color: var(--color-primary);
  font-weight: 600;
}

.record-result {
  margin-left: auto;
  color: var(--color-text-tertiary);
}

.record-result.failed {
  color: var(--color-danger);
}

.record-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 10px 0;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.record-model {
  overflow: hidden;
  margin-bottom: 10px;
  color: var(--color-text-secondary);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.record-foot > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.record-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-top: 1px solid var(--color-border-light);
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.record-pager button {
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 18px;
  cursor: pointer;
}

.record-pager button:disabled {
  opacity: 0.35;
  cursor: default;
}

.record-reader {
  min-width: 0;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg-secondary);
  scrollbar-width: thin;
}

.records-empty {
  padding: 40px 20px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.9;
  text-align: center;
}

.reader-placeholder {
  display: flex;
  min-height: 420px;
  padding: 40px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
}

.reader-placeholder strong {
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 500;
}

.reader-placeholder p {
  max-width: 280px;
  margin: 12px 0 20px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.9;
}

.reader-empty-icon {
  margin-bottom: 20px;
  color: var(--color-text-tertiary);
  font-size: 30px;
}

.loading-line {
  width: 65%;
  height: 12px;
  margin-bottom: 14px;
  border-radius: 4px;
  background: var(--color-bg-tertiary);
}

.loading-line.short {
  width: 40%;
}

@media (max-width: 1100px) {
  .reasoning-layout {
    grid-template-columns: 280px minmax(0, 1fr);
    gap: 12px;
  }
}

@media (max-width: 760px) {
  .reasoning-layout {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
    min-height: 0;
  }
  .record-list {
    max-height: 330px;
  }
  .record-reader {
    overflow: visible;
  }
  .reasoning-toolbar p {
    font-size: 11px;
    line-height: 1.7;
  }
  .reader-placeholder {
    min-height: 240px;
    padding: 24px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .record-card {
    transition: none;
  }
}
</style>
