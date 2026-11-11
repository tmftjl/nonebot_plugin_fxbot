<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ChatDotRound, Delete, Plus, Position, Refresh, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { localChatApi } from '@/api/localChat'
import type { LocalChatEvent, LocalChatMessage } from '@/types/api'

interface Conversation { id: string; title: string }
const STORAGE = 'fxbot-console-local-conversations'
const ACTIVE = 'fxbot-console-local-active'
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const conversations = ref<Conversation[]>([])
try {
  const saved: unknown = JSON.parse(localStorage.getItem(STORAGE) || '[]')
  if (Array.isArray(saved)) conversations.value = saved.filter(
    (row): row is Conversation => row && typeof row.id === 'string' && uuidPattern.test(row.id) && typeof row.title === 'string',
  ).slice(0, 20)
} catch { /* 本地列表损坏时不影响后台历史。 */ }

const active = ref(localStorage.getItem(ACTIVE) || '')
const messages = ref<LocalChatMessage[]>([])
const draft = ref('')
const nickname = ref('控制台用户')
const persona = ref('')
const provider = ref('')
const personas = ref<string[]>([])
const providers = ref<string[]>([])
const effectivePersona = ref('FxBot')
const canChat = ref(true)
const busy = ref(false)
const loading = ref(false)
const clearing = ref(false)
const status = ref('')
const channelError = ref('')
const historyElement = ref<HTMLElement>()
const messageId = ref('')
const canSend = computed(() => !!active.value && !!draft.value.trim() && !busy.value && !loading.value && !clearing.value && canChat.value)
let controller: AbortController | null = null
let revision = 0
let disposed = false
let reconnectTimer: ReturnType<typeof setTimeout> | undefined

function uuid() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function saveList() {
  localStorage.setItem(STORAGE, JSON.stringify(conversations.value))
  localStorage.setItem(ACTIVE, active.value)
}

async function scrollBottom() {
  await nextTick()
  const element = historyElement.value
  if (element) element.scrollTop = element.scrollHeight
}

async function refreshHistory(keepLive = false) {
  clearTimeout(reconnectTimer)
  const id = active.value
  const current = revision
  loading.value = true
  try {
    const state = await localChatApi.history(id)
    if (disposed || id !== active.value || current !== revision) return
    if (!keepLive || state.items.length) messages.value = state.items
    persona.value = state.persona
    provider.value = state.provider
    effectivePersona.value = state.effective_persona || 'FxBot'
    canChat.value = state.can_chat
    busy.value = state.running
    messageId.value = state.message_id || ''
    if (state.running) {
      status.value = '会话仍在处理中，可停止或稍后刷新'
      clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(() => { void refreshHistory() }, 2000)
    }
    await scrollBottom()
  } catch {
    if (!disposed && id === active.value && current === revision) channelError.value = '加载对话失败，请重试'
  } finally {
    if (!disposed && id === active.value && current === revision) loading.value = false
  }
}

async function selectConversation(id: string) {
  if (busy.value || clearing.value) return
  revision++
  clearTimeout(reconnectTimer)
  active.value = id
  messages.value = []
  status.value = ''
  channelError.value = ''
  saveList()
  await refreshHistory()
}

async function newConversation() {
  if (busy.value || clearing.value) return
  const id = uuid()
  conversations.value = [{ id, title: '新对话' }, ...conversations.value].slice(0, 20)
  await selectConversation(id)
}

function receive(event: LocalChatEvent) {
  if (event.type === 'accepted' || event.type === 'message') {
    const role = event.type === 'accepted' ? 'user' : 'assistant'
    messages.value.push({ id: event.id || uuid(), role, content: event.content || '' })
    if (role === 'user') {
      const conversation = conversations.value.find((item) => item.id === active.value)
      if (conversation?.title === '新对话') {
        conversation.title = (event.content || '新对话').slice(0, 20)
        saveList()
      }
    }
    void scrollBottom()
  } else if (event.type === 'status') {
    status.value = event.content || ''
  } else if (event.type === 'error') {
    channelError.value = '本轮对话未能完成，请检查模型配置后重试'
  }
}

async function send() {
  if (!canSend.value) return
  const id = active.value
  clearTimeout(reconnectTimer)
  const text = draft.value
  const current = ++revision
  const abort = new AbortController()
  controller = abort
  messageId.value = uuid()
  busy.value = true
  channelError.value = ''
  status.value = '正在接收消息'
  draft.value = ''
  let accepted = false
  try {
    await localChatApi.send(id, {
      text, message_id: messageId.value, nickname: nickname.value || '控制台用户',
      persona: persona.value, provider: provider.value,
    }, (event) => {
      if (disposed || current !== revision || id !== active.value) return
      if (event.type === 'accepted') accepted = true
      receive(event)
    }, abort.signal)
  } catch {
    if (!disposed && current === revision && !abort.signal.aborted) {
      channelError.value = '发送失败，请检查 AI 开关、人格和模型配置后重试'
      if (!accepted && !draft.value) draft.value = text
    }
  } finally {
    if (!disposed && current === revision && id === active.value) {
      controller = null
      busy.value = false
      status.value = abort.signal.aborted ? '已停止' : status.value === '本轮没有发送回复' ? status.value : ''
      await refreshHistory(true)
    }
  }
}

async function stop() {
  const id = active.value
  const targetMessage = messageId.value
  controller?.abort()
  try {
    if (targetMessage) await localChatApi.stop(id, targetMessage)
    if (!disposed && id === active.value) await refreshHistory()
  } catch { ElMessage.error('停止失败，请重试') }
}

async function clearConversation() {
  if (clearing.value) return
  const id = active.value
  try {
    await ElMessageBox.confirm('清空本会话的短期历史，并停止正在进行的回复？不会删除其他聊天或长期记忆。', '清空对话', { type: 'warning' })
  } catch { return }
  if (disposed || id !== active.value) return
  revision++
  controller?.abort()
  controller = null
  clearTimeout(reconnectTimer)
  clearing.value = true
  try {
    const result = await localChatApi.clear(id)
    messages.value = []
    busy.value = false
    status.value = ''
    channelError.value = result.history_cleared ? '' : '内存历史已清空，但持久化清理未确认成功'
    await refreshHistory()
  } catch { ElMessage.error('清空失败，请重试') }
  finally { clearing.value = false }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    void send()
  }
}

onMounted(async () => {
  try {
    const options = await localChatApi.options()
    if (disposed) return
    personas.value = options.personas
    providers.value = options.providers
  } catch { channelError.value = '加载人格和模型选项失败，请刷新重试' }
  if (disposed) return
  if (!conversations.value.some((item) => item.id === active.value)) {
    active.value = conversations.value[0]?.id || ''
  }
  if (!active.value) await newConversation()
  else await selectConversation(active.value)
})

onUnmounted(() => {
  disposed = true
  revision++
  clearTimeout(reconnectTimer)
  controller?.abort()
})
</script>

<template>
  <div class="local-chat-page">
    <PageHeader title="直接对话" subtitle="独立 WebUI 会话 · 复用规划、人格与记忆 · 无需真实 Bot 在线">
      <template #actions>
        <el-button :icon="Plus" :disabled="busy || clearing" @click="newConversation">新对话</el-button>
        <el-button :icon="Delete" :disabled="loading || clearing" @click="clearConversation">清空</el-button>
      </template>
    </PageHeader>

    <div class="local-chat-layout">
      <aside class="conversation-list">
        <span class="list-label">本机对话</span>
        <button v-for="item in conversations" :key="item.id" type="button" class="conversation-item"
          :class="{ active: item.id === active }" :disabled="busy || clearing" @click="selectConversation(item.id)">
          <el-icon><ChatDotRound /></el-icon><span>{{ item.title }}</span>
        </button>
        <p class="list-help">会话入口保存在此浏览器；历史沿用系统短期上下文配置。</p>
      </aside>

      <section class="chat-panel">
        <div class="chat-toolbar">
          <el-select v-model="persona" placeholder="全局人格" :disabled="busy || loading || messages.length > 0" aria-label="人格">
            <el-option label="全局人格" value="" /><el-option v-for="name in personas" :key="name" :label="name" :value="name" />
          </el-select>
          <el-select v-model="provider" placeholder="全局模型" :disabled="busy || loading" aria-label="回复模型">
            <el-option label="全局模型" value="" /><el-option v-for="name in providers" :key="name" :label="name" :value="name" />
          </el-select>
          <el-input v-model="nickname" maxlength="32" placeholder="你的昵称" :disabled="busy" aria-label="你的昵称" />
          <el-button :icon="Refresh" circle :disabled="busy || loading" aria-label="刷新历史" @click="refreshHistory()" />
        </div>

        <el-alert v-if="!canChat" title="AI 已关闭，或当前不在允许回复的时间内。可在对话设置中检查。" type="warning" :closable="false" />
        <el-alert v-if="channelError" :title="channelError" type="error" :closable="false" />

        <div ref="historyElement" class="chat-history" role="log" aria-live="polite" :aria-busy="busy">
          <div v-if="!messages.length && !loading" class="chat-empty">
            <div class="empty-icon"><el-icon><ChatDotRound /></el-icon></div>
            <h2>和 {{ effectivePersona }} 聊一会儿</h2>
            <p>从一句问候开始。这里的消息不会发到 QQ 或其他真实平台。</p>
          </div>
          <p v-if="loading" class="chat-status">正在加载历史…</p>
          <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role">
            <span class="message-author">{{ message.role === 'user' ? nickname : effectivePersona }}</span>
            <div class="message-body">{{ message.content }}</div>
          </article>
          <div v-if="busy" class="chat-status"><span class="activity-dot" />{{ status || '正在思考' }}…</div>
          <div v-else-if="status" class="chat-status">{{ status }}</div>
        </div>

        <div class="chat-composer">
          <textarea v-model="draft" maxlength="8000" rows="3" placeholder="输入消息，Enter 发送，Shift + Enter 换行"
            aria-label="消息内容" :disabled="clearing" @keydown="onKeydown" />
          <div class="composer-footer">
            <span>仅文字收发 · 不赋予真实平台操作权限</span>
            <el-button v-if="busy" :icon="VideoPause" @click="stop">停止</el-button>
            <el-button v-else type="primary" :icon="Position" :disabled="!canSend" @click="send">发送</el-button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.local-chat-page { padding: 24px 28px 32px; }
.local-chat-layout { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 16px; }
.conversation-list, .chat-panel { border: 1px solid var(--color-border); border-radius: var(--radius-xl); background: var(--color-bg); }
.conversation-list { padding: 16px 10px; }
.list-label { display: block; margin: 0 10px 12px; color: var(--color-text-tertiary); font-size: 12px; }
.conversation-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 11px 10px; margin-bottom: 4px; border: 0; border-radius: var(--radius-md); background: transparent; color: var(--color-text-secondary); text-align: left; cursor: pointer; }
.conversation-item span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-item.active { background: var(--color-primary-soft); color: var(--color-primary); }
.conversation-item:disabled { cursor: default; }
.list-help { color: var(--color-text-tertiary); font-size: 12px; line-height: 1.7; margin: 22px 10px 0; }
.chat-panel { display: flex; flex-direction: column; min-width: 0; height: min(760px, calc(100dvh - 200px)); min-height: 430px; overflow: hidden; }
.chat-toolbar { display: flex; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--color-border); }
.chat-toolbar .el-select { width: 180px; }.chat-toolbar .el-input { width: 130px; }
.chat-history { flex: 1; min-height: 0; overflow-y: auto; padding: 24px; }
.chat-empty { text-align: center; margin: 70px auto; color: var(--color-text-secondary); }
.chat-empty h2 { color: var(--color-text); font-size: 21px; font-weight: 600; margin: 16px 0 8px; }
.chat-empty p { font-size: 13px; line-height: 1.8; }
.empty-icon { display: inline-flex; padding: 15px; border-radius: 18px; background: var(--color-primary-soft); color: var(--color-primary); font-size: 30px; }
.chat-message { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin-bottom: 22px; }
.chat-message.user { align-items: flex-end; }
.message-author { color: var(--color-text-tertiary); font-size: 12px; }
.message-body { max-width: min(86%, 780px); white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; padding: 12px 16px; background: var(--color-bg-tertiary); border-radius: 12px; color: var(--color-text); font-size: 14px; }
.user .message-body { background: var(--color-primary); color: white; }
.chat-status { display: flex; align-items: center; gap: 8px; color: var(--color-text-tertiary); font-size: 12px; margin: 16px 0; }
.activity-dot { height: 7px; width: 7px; border-radius: 50%; background: var(--color-primary); }
.chat-composer { border-top: 1px solid var(--color-border); padding: 14px 18px; }
.chat-composer textarea { width: 100%; resize: vertical; min-height: 60px; max-height: 180px; box-sizing: border-box; border: 0; outline: none; background: transparent; color: var(--color-text); font: inherit; font-size: 14px; line-height: 1.7; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; }
.composer-footer > span { color: var(--color-text-tertiary); font-size: 12px; }
@media (max-width: 1000px) { .local-chat-layout { grid-template-columns: 150px minmax(0, 1fr); }.chat-toolbar { flex-wrap: wrap; }.chat-toolbar .el-select { flex: 1; min-width: 120px; } }
@media (max-width: 680px) { .local-chat-layout { grid-template-columns: 1fr; }.conversation-list { display: flex; gap: 6px; padding: 8px; overflow-x: auto; }.conversation-item { width: auto; flex-shrink: 0; margin: 0; max-width: 180px; }.list-label, .list-help { display: none; }.chat-panel { height: calc(100dvh - 300px); }.chat-history { padding: 16px; }.composer-footer > span { max-width: 180px; }.message-body { max-width: 92%; } }
@media (max-width: 680px) { .local-chat-page { padding: 18px 12px 24px; } }
</style>
