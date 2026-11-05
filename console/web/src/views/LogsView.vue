<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import ListPager from '@/components/ListPager.vue'
import { logsApi, getToken, type LogHistoryItem } from '@/api'

interface LogEntry {
  boot: string
  seq: number
  time: string
  level: 'TRACE' | 'DEBUG' | 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL'
  name: string
  text: string
}

type LevelFilter = 'ALL' | 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
type LogMode = 'live' | 'history'
type HistoryLevel = 'INFO' | 'WARNING' | 'ERROR'

const MAX_LOGS = 2000
const FLUSH_MS = 100
const HISTORY_PAGE_SIZE = 50

const liveLevels: { key: LevelFilter; label: string }[] = [
  { key: 'ALL', label: '全部' },
  { key: 'DEBUG', label: 'DEBUG' },
  { key: 'INFO', label: 'INFO' },
  { key: 'WARNING', label: '警告' },
  { key: 'ERROR', label: '错误' },
]

const historyLevels: { key: HistoryLevel; label: string }[] = [
  { key: 'INFO', label: 'INFO' },
  { key: 'WARNING', label: '警告' },
  { key: 'ERROR', label: '错误' },
]

const mode = ref<LogMode>('live')
const logs = ref<LogEntry[]>([])
const levelFilter = ref<LevelFilter>('ALL')
const keyword = ref('')
const paused = ref(false)
const connectionStatus = ref('连接中')
const newLogCount = ref(0)
const lossNotice = ref('')
const logContainer = ref<HTMLElement | null>(null)

const historyLevel = ref<HistoryLevel>('INFO')
const historyKeyword = ref('')
const sinceInput = ref('')
const untilInput = ref('')
const historyItems = ref<LogHistoryItem[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const loadingHistory = ref(false)

let eventSource: EventSource | null = null
let lastSeq: number | null = null
let lastBoot: string | null = null
let lostCount = 0
let userAtBottom = true
let pending: LogEntry[] = []
let flushTimer: number | null = null

const levelRank: Record<LogEntry['level'], number> = {
  TRACE: 0,
  DEBUG: 0,
  INFO: 1,
  SUCCESS: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 3,
}

const filterRank: Record<Exclude<LevelFilter, 'ALL'>, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
}

const connected = computed(() => connectionStatus.value === '已连接')

const subtitle = computed(() =>
  mode.value === 'live'
    ? '实时追踪进程输出，最多回放最近 2000 条'
    : 'INFO 及以上自动落库，保留 7 天',
)

const filteredLogs = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return logs.value.filter((log) => {
    const levelMatch = levelFilter.value === 'ALL'
      || levelRank[log.level] >= filterRank[levelFilter.value]
    const keywordMatch = !query
      || `${log.time} ${log.level} ${log.name} ${log.text}`.toLowerCase().includes(query)
    return levelMatch && keywordMatch
  })
})

const isNearBottom = () => {
  const element = logContainer.value
  if (!element) return true
  return element.scrollHeight - element.scrollTop - element.clientHeight < 32
}

const handleScroll = () => {
  userAtBottom = isNearBottom()
  if (userAtBottom) newLogCount.value = 0
}

const scrollToBottom = () => {
  nextTick(() => {
    const element = logContainer.value
    if (element) element.scrollTop = element.scrollHeight
    userAtBottom = true
    newLogCount.value = 0
  })
}

// 进页面时会整段重放缓冲区（最多 2000 条），逐条写进响应式数组会让列表每来一条就
// 重渲染并强制回流一次，重放期间页面直接卡死；按帧攒批，一次合并进列表
const flushPending = () => {
  flushTimer = null
  const batch = pending
  pending = []
  let appended = 0
  for (const entry of batch) {
    // boot 变化说明服务端重启了，而 seq 每次重启都从 1 重新计数；不重置 lastSeq 的话
    // 新日志会被下面的「重复」判断全部丢弃，页面从此静默
    if (entry.boot !== lastBoot) {
      logs.value = []
      lossNotice.value = ''
      newLogCount.value = 0
      lastSeq = null
      lostCount = 0
      lastBoot = entry.boot
    }
    if (!Number.isFinite(entry.seq) || (lastSeq !== null && entry.seq <= lastSeq)) continue
    if (lastSeq !== null && entry.seq !== lastSeq + 1) {
      lostCount += entry.seq - lastSeq - 1
      if (!lossNotice.value) ElMessage.warning('日志有丢失')
      lossNotice.value = `日志有丢失（累计 ${lostCount} 条）`
    }
    lastSeq = entry.seq
    logs.value.push(entry)
    appended += 1
  }
  if (!appended) return
  if (logs.value.length > MAX_LOGS) logs.value.splice(0, logs.value.length - MAX_LOGS)
  if (!paused.value && userAtBottom) {
    scrollToBottom()
  } else {
    newLogCount.value += appended
  }
}

const scheduleFlush = () => {
  if (flushTimer === null) flushTimer = window.setTimeout(flushPending, FLUSH_MS)
}

const handleMessage = (event: MessageEvent<string>) => {
  try {
    const entry = JSON.parse(event.data) as LogEntry
    // 页面在后台时定时器会被节流，攒到超过缓冲上限就丢最旧的，seq 断档会走丢失提示
    if (pending.length >= MAX_LOGS) pending.splice(0, pending.length - MAX_LOGS + 1)
    pending.push(entry)
    scheduleFlush()
  } catch {
    ElMessage.warning('收到无法解析的日志')
  }
}

const connect = () => {
  const token = encodeURIComponent(getToken())
  eventSource = new EventSource(`/fxbot/logs/stream?token=${token}`)
  eventSource.onopen = () => {
    connectionStatus.value = '已连接'
  }
  eventSource.onmessage = handleMessage
  eventSource.onerror = () => {
    // CLOSED 表示浏览器已放弃重连（token 失效被拒 401/403 时就是这种），
    // 此时还报「重连中」会一直骗人；CONNECTING 才是它会自己退避重试
    connectionStatus.value = eventSource?.readyState === EventSource.CLOSED ? '已断开' : '重连中'
  }
}

const clearLogs = () => {
  logs.value = []
  lossNotice.value = ''
  newLogCount.value = 0
  lostCount = 0
}

const togglePaused = () => {
  paused.value = !paused.value
  if (!paused.value && userAtBottom) scrollToBottom()
}

const formatTs = (ts: string): string => (ts ? ts.slice(5, 19).replace('T', ' ') : '')

const fetchHistory = async (page = historyPage.value) => {
  loadingHistory.value = true
  historyPage.value = page
  try {
    const res = await logsApi.history({
      level: historyLevel.value,
      keyword: historyKeyword.value,
      since: sinceInput.value ? new Date(sinceInput.value).getTime() : null,
      until: untilInput.value ? new Date(untilInput.value).getTime() : null,
      page,
      pageSize: HISTORY_PAGE_SIZE,
    })
    historyItems.value = res.items || []
    historyTotal.value = res.total || 0
  } catch (e: any) {
    ElMessage.error('加载历史日志失败: ' + (e?.message || e))
  } finally {
    loadingHistory.value = false
  }
}

const setMode = (value: LogMode) => {
  if (value === mode.value) {
    // 再点当前分类时强制刷新，避免看起来「点了没反应」
    if (value === 'history') fetchHistory(1)
    else scrollToBottom()
    return
  }
  mode.value = value
  if (value === 'history') fetchHistory(1)
  else scrollToBottom()
}

const resetHistoryFilters = () => {
  historyLevel.value = 'INFO'
  historyKeyword.value = ''
  sinceInput.value = ''
  untilInput.value = ''
  fetchHistory(1)
}

const clearHistory = async () => {
  try {
    await ElMessageBox.confirm('确认清空已落库的历史日志？', '清空历史日志', { type: 'warning' })
  } catch {
    return
  }
  try {
    await logsApi.clearHistory()
    ElMessage.success('已清空历史日志')
    fetchHistory(1)
  } catch {
    ElMessage.error('清空失败，请稍后再试')
  }
}

onMounted(() => {
  connect()
})

onUnmounted(() => {
  eventSource?.close()
  eventSource = null
  if (flushTimer !== null) window.clearTimeout(flushTimer)
  flushTimer = null
  pending = []
})
</script>

<template>
  <div class="logs-view">
    <PageHeader title="运行日志" :subtitle="subtitle">
      <template #actions>
        <div class="mode-switch" role="tablist" aria-label="日志类别">
          <button
            type="button"
            role="tab"
            class="mode-btn"
            :class="{ active: mode === 'live' }"
            :aria-selected="mode === 'live'"
            @click="setMode('live')"
          >
            <span class="mode-dot" :class="{ online: connected }" />
            实时
          </button>
          <button
            type="button"
            role="tab"
            class="mode-btn"
            :class="{ active: mode === 'history' }"
            :aria-selected="mode === 'history'"
            @click="setMode('history')"
          >
            历史记录
          </button>
        </div>
      </template>
    </PageHeader>

    <section class="log-panel">
      <div v-if="mode === 'live'" class="log-toolbar">
        <div class="chips" role="group" aria-label="级别筛选">
          <button
            v-for="item in liveLevels"
            :key="item.key"
            type="button"
            class="chip"
            :class="{ active: levelFilter === item.key }"
            @click="levelFilter = item.key"
          >
            {{ item.label }}
          </button>
        </div>
        <input v-model="keyword" class="log-input" placeholder="搜索日志关键字" />
        <button class="btn btn-sm" type="button" @click="togglePaused">
          {{ paused ? '继续滚动' : '暂停滚动' }}
        </button>
        <button class="btn btn-sm" type="button" @click="clearLogs">清屏</button>
        <div class="conn">
          <span class="conn-dot" :class="{ online: connected }" />
          <span>{{ connectionStatus }}</span>
          <span class="conn-count">{{ filteredLogs.length }} 行</span>
        </div>
      </div>

      <div v-else class="log-toolbar">
        <div class="chips" role="group" aria-label="级别筛选">
          <button
            v-for="item in historyLevels"
            :key="item.key"
            type="button"
            class="chip"
            :class="{ active: historyLevel === item.key }"
            @click="historyLevel = item.key"
          >
            {{ item.label }}
          </button>
        </div>
        <input
          v-model="historyKeyword"
          class="log-input"
          placeholder="搜索日志关键字"
          @keyup.enter="fetchHistory(1)"
        />
        <input v-model="sinceInput" type="datetime-local" class="log-input time-input" aria-label="开始时间" />
        <span class="time-sep">至</span>
        <input v-model="untilInput" type="datetime-local" class="log-input time-input" aria-label="结束时间" />
        <button class="btn btn-sm btn-primary" type="button" :disabled="loadingHistory" @click="fetchHistory(1)">
          查询
        </button>
        <button class="btn btn-sm" type="button" @click="resetHistoryFilters">重置</button>
        <button class="ghost-danger" type="button" @click="clearHistory">清空历史</button>
        <div class="conn">
          <span class="conn-count">共 {{ historyTotal }} 条</span>
        </div>
      </div>

      <div v-if="mode === 'live' && lossNotice" class="loss-notice">{{ lossNotice }}</div>

      <template v-if="mode === 'live'">
        <div class="log-head">
          <span>时间</span>
          <span>级别</span>
          <span>模块</span>
          <span>内容</span>
        </div>
        <div ref="logContainer" class="log-list" @scroll="handleScroll">
          <div v-if="!filteredLogs.length" class="empty-state log-empty">
            <div class="empty-icon">📄</div>
            <div class="empty-title">暂无日志</div>
          </div>
          <div v-for="log in filteredLogs" :key="log.seq" class="log-row">
            <span class="log-time">{{ log.time }}</span>
            <span class="log-level"><span class="badge" :class="`badge-${log.level.toLowerCase()}`">{{ log.level }}</span></span>
            <span class="log-name" :title="log.name">{{ log.name || '—' }}</span>
            <span class="log-text">{{ log.text }}</span>
          </div>
        </div>
        <button v-if="newLogCount" class="new-logs" type="button" @click="scrollToBottom">
          查看 {{ newLogCount }} 条新日志
        </button>
      </template>

      <template v-else>
        <div class="log-head">
          <span>时间</span>
          <span>级别</span>
          <span>模块</span>
          <span>内容</span>
        </div>
        <div class="log-list">
          <div v-if="!historyItems.length" class="empty-state log-empty">
            <div class="empty-icon">🗂️</div>
            <div class="empty-title">{{ loadingHistory ? '加载中…' : '暂无历史日志' }}</div>
          </div>
          <div v-for="log in historyItems" :key="log.id" class="log-row">
            <span class="log-time">{{ formatTs(log.ts) }}</span>
            <span class="log-level"><span class="badge" :class="`badge-${log.level.toLowerCase()}`">{{ log.level }}</span></span>
            <span class="log-name" :title="log.name">{{ log.name || '—' }}</span>
            <span class="log-text">{{ log.text }}</span>
          </div>
        </div>
        <div class="log-footer">
          <span class="footer-hint">第 {{ historyPage }} 页 · 每页 {{ HISTORY_PAGE_SIZE }} 条</span>
          <ListPager
            :page="historyPage"
            :total="historyTotal"
            :page-size="HISTORY_PAGE_SIZE"
            @update:page="fetchHistory"
          />
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped lang="scss">
.logs-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
  padding: 22px 28px 24px;
}

.mode-switch {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
}

.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;

  &:hover {
    color: var(--color-text);
  }

  &.active {
    background: var(--color-bg-card);
    color: var(--color-primary);
    font-weight: 600;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
  }
}

.mode-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-tertiary);

  &.online {
    background: var(--color-success);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-success) 18%, transparent);
  }
}

.log-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

.log-toolbar {
  display: flex;
  flex: none;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-light);
}

.chips {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
}

.chip {
  height: 26px;
  padding: 0 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;

  &:hover {
    color: var(--color-text);
  }

  &.active {
    background: var(--color-bg-card);
    color: var(--color-primary);
    font-weight: 600;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
  }
}

.log-input {
  height: 32px;
  min-width: 180px;
  flex: 1 1 200px;
  max-width: 320px;
  box-sizing: border-box;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
  color: var(--color-text);
  font-family: inherit;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease;

  &:focus {
    border-color: var(--color-primary);
  }

  &::placeholder {
    color: var(--color-text-tertiary);
  }
}

.time-input {
  flex: 0 0 auto;
  width: 188px;
  min-width: 0;
  max-width: none;
  font-size: 12px;
}

.time-sep {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.conn {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.conn-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-warning);

  &.online {
    background: var(--color-success);
  }
}

.conn-count {
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.ghost-danger {
  height: 30px;
  padding: 0 8px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-danger);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;

  &:hover {
    background: color-mix(in srgb, var(--color-danger) 10%, transparent);
  }
}

.loss-notice {
  flex: none;
  padding: 6px 16px;
  border-bottom: 1px solid var(--color-border-light);
  background: color-mix(in srgb, var(--color-danger) 8%, transparent);
  color: var(--color-danger);
  font-size: 12px;
}

.log-head {
  display: grid;
  flex: none;
  grid-template-columns: 118px 78px minmax(140px, 0.8fr) minmax(320px, 2.4fr);
  gap: 14px;
  padding: 8px 18px;
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.log-list {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--color-bg);
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;

  &::-webkit-scrollbar {
    display: block;
    width: 8px;
  }

  &::-webkit-scrollbar-thumb {
    border-radius: 4px;
    background: var(--color-border);
  }
}

.log-row {
  display: grid;
  grid-template-columns: 118px 78px minmax(140px, 0.8fr) minmax(320px, 2.4fr);
  gap: 14px;
  padding: 7px 18px;
  border-bottom: 1px solid var(--color-border-lighter);
  font-family: 'JetBrains Mono', Monaco, Consolas, 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;

  &:hover {
    background: var(--color-bg-hover);
  }
}

.log-time {
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.log-level {
  display: flex;
  align-items: baseline;
}

.log-name {
  overflow: hidden;
  color: var(--color-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-text {
  color: var(--color-text);
}

.badge {
  display: inline-flex;
  justify-content: center;
  min-width: 62px;
  padding: 1px 7px;
  border-radius: 999px;
  font-family: var(--el-font-family);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
  line-height: 1.5;
}

.badge-trace,
.badge-debug {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}

.badge-info {
  background: color-mix(in srgb, var(--color-info) 12%, transparent);
  color: var(--color-info);
}

.badge-success {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
}

.badge-warning {
  background: color-mix(in srgb, var(--color-warning) 14%, transparent);
  color: var(--color-warning);
}

.badge-error,
.badge-critical {
  background: color-mix(in srgb, var(--color-danger) 12%, transparent);
  color: var(--color-danger);
}

.log-empty {
  padding: 72px 20px;
}

.new-logs {
  flex: none;
  align-self: center;
  margin: 10px 0 12px;
  padding: 6px 16px;
  border: 1px solid var(--color-primary);
  border-radius: 999px;
  background: var(--color-bg-card);
  color: var(--color-primary);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 14px color-mix(in srgb, var(--color-primary) 18%, transparent);

  &:hover {
    background: var(--color-primary-soft);
  }
}

.log-footer {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-top: 1px solid var(--color-border-light);
  background: var(--color-bg-card);
}

.footer-hint {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

@media (max-width: 900px) {
  .logs-view {
    padding: 16px;
  }

  .log-toolbar {
    padding: 10px 12px;
  }

  .log-head {
    display: none;
  }

  .log-row {
    grid-template-columns: 1fr;
    gap: 4px;
    padding: 10px 14px;
  }

  .time-input {
    width: 100%;
  }

  .conn {
    margin-left: 0;
  }
}
</style>
