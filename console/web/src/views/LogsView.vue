<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getToken } from '@/api'

interface LogEntry {
  boot: string
  seq: number
  time: string
  level: 'TRACE' | 'DEBUG' | 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL'
  name: string
  text: string
}

type LevelFilter = 'ALL' | 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'

const logs = ref<LogEntry[]>([])
const levelFilter = ref<LevelFilter>('ALL')
const keyword = ref('')
const paused = ref(false)
const connectionStatus = ref('连接中')
const newLogCount = ref(0)
const lossNotice = ref('')
const logContainer = ref<HTMLElement | null>(null)
let eventSource: EventSource | null = null
let lastSeq: number | null = null
let lastBoot: string | null = null
let lostCount = 0
let userAtBottom = true

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

const appendLog = (entry: LogEntry) => {
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
  if (!Number.isFinite(entry.seq) || lastSeq !== null && entry.seq <= lastSeq) return
  if (lastSeq !== null && entry.seq !== lastSeq + 1) {
    lostCount += entry.seq - lastSeq - 1
    if (!lossNotice.value) ElMessage.warning('日志有丢失')
    lossNotice.value = `日志有丢失（累计 ${lostCount} 条）`
  }
  lastSeq = entry.seq
  logs.value.push(entry)
  if (logs.value.length > 2000) logs.value.splice(0, logs.value.length - 2000)
  if (!paused.value && userAtBottom) {
    scrollToBottom()
  } else {
    newLogCount.value += 1
  }
}

const handleMessage = (event: MessageEvent<string>) => {
  try {
    appendLog(JSON.parse(event.data) as LogEntry)
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

onMounted(() => {
  connect()
})

onUnmounted(() => {
  eventSource?.close()
  eventSource = null
})
</script>

<template>
  <div class="logs-view">
    <div class="panel">
      <div class="toolbar">
        <select v-model="levelFilter" class="input level-select" aria-label="级别阈值">
          <option value="ALL">全部</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
        <input v-model="keyword" class="input keyword-input" placeholder="搜索日志关键字" />
        <button class="btn" type="button" @click="togglePaused">{{ paused ? '继续滚动' : '暂停滚动' }}</button>
        <button class="btn" type="button" @click="clearLogs">清空</button>
        <div class="toolbar-status">
          <span class="status-dot" :class="{ connected: connectionStatus === '已连接' }" />
          <span>{{ connectionStatus }}</span>
          <span>{{ filteredLogs.length }} 行</span>
        </div>
      </div>
      <div v-if="lossNotice" class="loss-notice">{{ lossNotice }}</div>
      <div ref="logContainer" class="log-list" @scroll="handleScroll">
        <div v-if="!filteredLogs.length" class="empty-state">暂无日志</div>
        <div v-for="log in filteredLogs" :key="log.seq" class="log-row" :class="`level-${log.level.toLowerCase()}`">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-level">{{ log.level }}</span>
          <span class="log-name">{{ log.name }}</span>
          <span class="log-text">{{ log.text }}</span>
        </div>
      </div>
      <button v-if="newLogCount" class="new-logs" type="button" @click="scrollToBottom">
        {{ newLogCount }} 条新日志
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.logs-view { height: 100%; box-sizing: border-box; display: flex; flex-direction: column; padding: 24px; }
.panel { height: 100%; min-height: 0; display: flex; flex-direction: column; background: var(--color-bg-card); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 20px; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.input { height: 34px; box-sizing: border-box; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-bg); color: var(--color-text); padding: 0 10px; }
.level-select { min-width: 110px; }
.keyword-input { width: 240px; }
.btn { height: 34px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-bg-tertiary); color: var(--color-text); padding: 0 14px; cursor: pointer; transition: var(--transition); &:hover { color: var(--color-primary); border-color: var(--color-primary); } }
.toolbar-status { display: flex; align-items: center; gap: 8px; margin-left: auto; color: var(--color-text-secondary); font-size: 13px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-warning); &.connected { background: var(--color-success); } }
.loss-notice { color: var(--color-danger); font-size: 13px; margin-bottom: 8px; }
.log-list { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 8px 4px; font-family: 'Courier New', monospace; scrollbar-width: thin; scrollbar-color: var(--color-border) var(--color-bg-tertiary); &::-webkit-scrollbar { display: block; width: 8px; } &::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 4px; } }
.log-row { display: grid; grid-template-columns: 125px 85px minmax(180px, 0.8fr) minmax(300px, 2fr); gap: 12px; padding: 5px 8px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
.log-time, .log-name { color: var(--color-text-secondary); }
.log-level { font-weight: 700; }
.log-text { color: var(--color-text); }
.level-trace .log-level, .level-debug .log-level { color: var(--color-text-tertiary); }
.level-info .log-level { color: var(--color-info); }
.level-success .log-level { color: var(--color-success); }
.level-warning .log-level { color: var(--color-warning); }
.level-error .log-level, .level-critical .log-level { color: var(--color-danger); }
.empty-state { padding: 40px; text-align: center; color: var(--color-text-tertiary); }
.new-logs { align-self: center; margin-top: 8px; border: 1px solid var(--color-primary); border-radius: var(--radius-md); background: var(--color-bg-card); color: var(--color-primary); padding: 6px 14px; cursor: pointer; }
</style>
