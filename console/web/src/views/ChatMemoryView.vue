<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Refresh, Delete } from '@element-plus/icons-vue'
import { chatApi } from '@/api'
import type {
  ChatTenant,
  ChatStyle,
  ChatJargon,
  ChatExpression,
  ChatBehavior,
  ChatPlannerSample,
  ChatReplyGate,
  ChatReplyGateObservation,
  ChatEmoji,
  ChatKnowledgeDoc,
  ChatReminder,
  ChatHealth,
  ChatJobs,
} from '@/types/api'

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
    enabled: false,
    gate_mode: 'observe',
    talk_value: 0,
    switch: null,
    group_allowed: false,
  },
})

const tenants = ref<ChatTenant[]>([])
const tenant = ref('')
const style = ref<ChatStyle | null>(null)
const jargon = ref<ChatJargon[]>([])
const jargonStatus = ref('all')
const expressions = ref<ChatExpression[]>([])
const expressionStatus = ref('all')
const plannerSamples = ref<ChatPlannerSample[]>([])
const behaviors = ref<ChatBehavior[]>([])
const behaviorAttributed = ref(0)
const behaviorMinimum = ref(50)
const replyGate = ref<ChatReplyGate>(emptyReplyGate())
const loading = ref(false)
const jargonLoading = ref(false)
const expressionLoading = ref(false)
const plannerLoading = ref(false)
const behaviorLoading = ref(false)
const gateLoading = ref(false)
const acting = ref(false)
const loaded = ref(false)
const emojis = ref<ChatEmoji[]>([])
const emojiLoading = ref(false)
const emojiUploading = ref(false)
const emojiDescription = ref('')
const knowledge = ref<ChatKnowledgeDoc[]>([])
const knowledgeLoading = ref(false)
const knowledgeUploading = ref(false)
const knowledgeTitle = ref('')
const knowledgeText = ref('')
const reminders = ref<ChatReminder[]>([])
const reminderLoading = ref(false)
const health = ref<ChatHealth | null>(null)
const jobs = ref<ChatJobs>({ jobs: [], cursors: [] })
const healthLoading = ref(false)

const plannerLabelCounts = computed(() => {
  const replies = plannerSamples.value.filter((row) => row.label === 1).length
  return { replies, silent: plannerSamples.value.length - replies }
})

const proactiveState = computed(() => {
  const cfg = replyGate.value.proactive
  return {
    effective: cfg.enabled && cfg.group_allowed && cfg.switch !== false,
    switchText: cfg.switch === null ? '跟随全局' : cfg.switch ? '开启' : '关闭',
  }
})

const formatTime = (value: string | null | undefined) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const formatCount = (value: number) => Number(value || 0).toFixed(3)

const expressionTag = (row: ChatExpression): { type: 'success' | 'warning' | 'info'; text: string } => {
  if (!row.checked) return { type: 'warning', text: '待复审' }
  return row.rejected ? { type: 'info', text: '已拒绝' } : { type: 'success', text: '生效' }
}

const loadTenants = async () => {
  try {
    const result = await chatApi.tenants()
    tenants.value = result.tenants || []
    if (!tenant.value && tenants.value.length) {
      tenant.value = tenants.value[0].tenant
    }
  } catch (error) {
    console.error('加载聊天租户失败', error)
    ElMessage.error('加载聊天租户失败，请重试')
  }
}

const loadJargon = async () => {
  if (!tenant.value) {
    jargon.value = []
    return
  }
  jargonLoading.value = true
  try {
    const result = await chatApi.listJargon(tenant.value, jargonStatus.value)
    jargon.value = result.items || []
  } catch (error) {
    console.error('加载黑话失败', error)
    ElMessage.error('加载黑话失败，请重试')
  } finally {
    jargonLoading.value = false
  }
}

const loadExpressions = async () => {
  if (!tenant.value) {
    expressions.value = []
    return
  }
  expressionLoading.value = true
  try {
    const result = await chatApi.listExpressions(tenant.value, expressionStatus.value)
    expressions.value = result.items || []
  } catch (error) {
    console.error('加载群表达失败', error)
    ElMessage.error('加载群表达失败，请重试')
  } finally {
    expressionLoading.value = false
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

const loadBehavior = async () => {
  if (!tenant.value) {
    behaviors.value = []
    behaviorAttributed.value = 0
    return
  }
  behaviorLoading.value = true
  try {
    const result = await chatApi.getBehavior(tenant.value)
    behaviors.value = result.items || []
    behaviorAttributed.value = result.attributed || 0
    behaviorMinimum.value = result.minimum || 50
  } catch (error) {
    console.error('加载行为样本失败', error)
    ElMessage.error('加载行为样本失败，请重试')
  } finally {
    behaviorLoading.value = false
  }
}

const clearBehavior = async () => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(
      `确定清空租户「${tenant.value}」的行为样本与聚合？清空后 Planner 不再收到行为参考。`,
      '清空行为样本',
      { confirmButtonText: '确定清空', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.clearBehavior(tenant.value)
    ElMessage.success('行为样本已清空')
    await loadBehavior()
  } catch (error) {
    console.error('清空行为样本失败', error)
    ElMessage.error('清空行为样本失败，请重试')
  } finally {
    acting.value = false
  }
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

const reminderTag = (status: string): { type: 'success' | 'warning' | 'info' | 'danger'; text: string } => {
  switch (status) {
    case 'sent':
      return { type: 'success', text: '已送达' }
    case 'failed':
      return { type: 'danger', text: '发送失败' }
    case 'claimed':
      return { type: 'warning', text: '发送中' }
    case 'cancelled':
      return { type: 'info', text: '已取消' }
    case 'pending_confirmation':
      return { type: 'warning', text: '待确认' }
    default:
      return { type: 'info', text: '待发送' }
  }
}

const loadEmoji = async () => {
  if (!tenant.value) {
    emojis.value = []
    return
  }
  emojiLoading.value = true
  try {
    const result = await chatApi.listEmoji(tenant.value)
    emojis.value = result.items || []
  } catch (error) {
    console.error('加载表情失败', error)
    ElMessage.error('加载表情失败，请重试')
  } finally {
    emojiLoading.value = false
  }
}

const readBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

const uploadEmojiFile = async (file: UploadFile) => {
  if (!file.raw || emojiUploading.value) return false
  if (!file.raw.type.startsWith('image/')) {
    ElMessage.warning('只能上传图片文件')
    return false
  }
  emojiUploading.value = true
  try {
    const content = await readBase64(file.raw)
    await chatApi.uploadEmoji(tenant.value, content, emojiDescription.value.trim())
    emojiDescription.value = ''
    ElMessage.success('表情已入库')
    await loadEmoji()
  } catch (error) {
    console.error('上传表情失败', error)
    ElMessage.error('上传表情失败，请重试')
  } finally {
    emojiUploading.value = false
  }
  return false
}

const toggleEmoji = async (row: ChatEmoji) => {
  if (acting.value) return
  acting.value = true
  try {
    await chatApi.setEmojiEnabled(tenant.value, row.hash, !row.enabled)
    row.enabled = !row.enabled
    ElMessage.success(row.enabled ? '表情已启用' : '表情已停用')
  } catch (error) {
    console.error('切换表情状态失败', error)
    ElMessage.error('切换表情状态失败，请重试')
  } finally {
    acting.value = false
  }
}

const deleteEmoji = async (row: ChatEmoji) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm('确定删除这条表情？文件与索引会一起移除。', '删除表情', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.deleteEmoji(tenant.value, row.hash)
    ElMessage.success('表情已删除')
    await loadEmoji()
  } catch (error) {
    console.error('删除表情失败', error)
    ElMessage.error('删除表情失败，请重试')
  } finally {
    acting.value = false
  }
}

const loadKnowledge = async () => {
  if (!tenant.value) {
    knowledge.value = []
    return
  }
  knowledgeLoading.value = true
  try {
    const result = await chatApi.listKnowledge(tenant.value)
    knowledge.value = result.items || []
  } catch (error) {
    console.error('加载知识文档失败', error)
    ElMessage.error('加载知识文档失败，请重试')
  } finally {
    knowledgeLoading.value = false
  }
}

const loadKnowledgeFile = (file: UploadFile) => {
  if (!file.raw) return false
  const reader = new FileReader()
  reader.onload = () => {
    knowledgeText.value = String(reader.result || '')
    if (!knowledgeTitle.value.trim()) {
      knowledgeTitle.value = file.name.replace(/\.[^.]+$/, '')
    }
  }
  reader.readAsText(file.raw)
  return false
}

const uploadKnowledge = async () => {
  if (knowledgeUploading.value) return
  if (!knowledgeText.value.trim()) {
    ElMessage.warning('请填写或上传文档内容')
    return
  }
  knowledgeUploading.value = true
  try {
    await chatApi.uploadKnowledge(tenant.value, knowledgeTitle.value.trim(), knowledgeText.value)
    knowledgeTitle.value = ''
    knowledgeText.value = ''
    ElMessage.success('文档已入库')
    await loadKnowledge()
  } catch (error) {
    console.error('导入知识文档失败', error)
    ElMessage.error('导入知识文档失败，请重试')
  } finally {
    knowledgeUploading.value = false
  }
}

const deleteKnowledge = async (row: ChatKnowledgeDoc) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(`确定删除文档「${row.title}」？删除后不再参与检索。`, '删除知识文档', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.deleteKnowledge(tenant.value, row.id)
    ElMessage.success('知识文档已删除')
    await loadKnowledge()
  } catch (error) {
    console.error('删除知识文档失败', error)
    ElMessage.error('删除知识文档失败，请重试')
  } finally {
    acting.value = false
  }
}

const loadReminders = async () => {
  if (!tenant.value) {
    reminders.value = []
    return
  }
  reminderLoading.value = true
  try {
    const result = await chatApi.listReminders(tenant.value)
    reminders.value = result.items || []
  } catch (error) {
    console.error('加载提醒失败', error)
    ElMessage.error('加载提醒失败，请重试')
  } finally {
    reminderLoading.value = false
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

const loadJobs = async () => {
  try {
    jobs.value = await chatApi.getJobs(tenant.value)
  } catch (error) {
    console.error('加载后台任务失败', error)
    jobs.value = { jobs: [], cursors: [] }
  }
}

const load = async () => {
  if (!tenant.value.trim()) {
    ElMessage.warning('请先选择或填写租户')
    return
  }
  tenant.value = tenant.value.trim()
  loading.value = true
  try {
    const [styleResult] = await Promise.all([
      chatApi.getStyle(tenant.value),
      loadJargon(),
      loadExpressions(),
      loadPlannerSamples(),
      loadBehavior(),
      loadReplyGate(),
      loadEmoji(),
      loadKnowledge(),
      loadReminders(),
      loadHealth(),
      loadJobs(),
    ])
    style.value = styleResult.style
    loaded.value = true
  } catch (error) {
    console.error('加载群风格失败', error)
    ElMessage.error('加载群风格失败，请重试')
  } finally {
    loading.value = false
  }
}

const refresh = async () => {
  if (loading.value) return
  await loadTenants()
  if (tenant.value) await load()
}

const deleteStyle = async () => {
  if (!style.value || acting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除租户「${tenant.value}」的群风格？删除后立即停止注入，下次学习会重新生成。`,
      '删除群风格',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.deleteStyle(tenant.value)
    ElMessage.success('群风格已删除')
    await load()
  } catch (error) {
    console.error('删除群风格失败', error)
    ElMessage.error('删除群风格失败，请重试')
  } finally {
    acting.value = false
  }
}

const deleteJargon = async (row: ChatJargon) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除黑话「${row.term}」？删除后当前消息不再注入该释义。`,
      '删除黑话',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.deleteJargon(tenant.value, row.id)
    ElMessage.success('黑话已删除')
    await loadJargon()
  } catch (error) {
    console.error('删除黑话失败', error)
    ElMessage.error('删除黑话失败，请重试')
  } finally {
    acting.value = false
  }
}

const reviewExpression = async (row: ChatExpression, rejected: boolean) => {
  if (acting.value) return
  acting.value = true
  try {
    await chatApi.reviewExpression(tenant.value, row.id, rejected)
    ElMessage.success(rejected ? '已驳回该表达' : '已通过该表达')
    await loadExpressions()
  } catch (error) {
    console.error('改判表达失败', error)
    ElMessage.error('改判表达失败，请重试')
  } finally {
    acting.value = false
  }
}

const deleteExpression = async (row: ChatExpression) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除表达「${row.style}」？删除后不再注入这条情境说法。`,
      '删除表达',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  acting.value = true
  try {
    await chatApi.deleteExpression(tenant.value, row.id)
    ElMessage.success('表达已删除')
    await loadExpressions()
  } catch (error) {
    console.error('删除表达失败', error)
    ElMessage.error('删除表达失败，请重试')
  } finally {
    acting.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="chat-memory-view">
    <div class="page-header">
      <h2>聊天记忆</h2>
      <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
    </div>

    <div class="panel tenant-panel">
      <div class="tenant-row">
        <span class="tenant-label">租户</span>
        <el-select
          v-model="tenant"
          class="tenant-select"
          filterable
          allow-create
          default-first-option
          placeholder="选择或输入 session_id"
        >
          <el-option
            v-for="item in tenants"
            :key="item.tenant"
            :label="item.tenant"
            :value="item.tenant"
          >
            <span>{{ item.tenant }}</span>
            <span class="tenant-count">{{ item.messages }} 条</span>
          </el-option>
        </el-select>
        <el-button type="primary" :loading="loading" @click="load">查看</el-button>
      </div>
      <div class="tenant-help">
        群聊和私聊各自独立；这里的租户等于 session_id，不会跨群合并。
      </div>
    </div>

    <div v-if="loaded" class="panel">
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
          记忆库
          <el-tag :type="health?.memory_ready ? 'success' : 'danger'" size="small">
            {{ health?.memory_ready ? '就绪' : '未就绪' }}
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

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">表情包</h3>
        <div class="panel-actions">
          <el-input
            v-model="emojiDescription"
            class="emoji-description"
            clearable
            placeholder="描述（留空由视觉模型生成）"
          />
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            :disabled="emojiUploading"
            accept="image/*"
            :on-change="uploadEmojiFile"
          >
            <el-button type="primary" plain :loading="emojiUploading">上传表情</el-button>
          </el-upload>
          <el-button :icon="Refresh" :loading="emojiLoading" @click="loadEmoji">刷新</el-button>
        </div>
      </div>
      <div class="tenant-help">按描述与标签匹配当前回复，同一表情冷却 5 分钟；发送失败时只保留文字。</div>
      <el-table v-loading="emojiLoading" :data="emojis" row-key="hash" empty-text="暂无表情">
        <el-table-column label="预览" width="96">
          <template #default="{ row }">
            <img class="emoji-preview" :src="chatApi.emojiImageUrl(tenant, row.hash)" :alt="row.description" />
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="标签" min-width="160">
          <template #default="{ row }">
            <el-tag v-for="tag in row.tags" :key="tag" size="small" type="info" class="rule-tag">{{ tag }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="发送" width="80" />
        <el-table-column label="最近发送" min-width="170">
          <template #default="{ row }">{{ formatTime(row.last_used) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" :disabled="acting" @change="toggleEmoji(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" :disabled="acting" @click="deleteEmoji(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">知识库</h3>
        <el-button :icon="Refresh" :loading="knowledgeLoading" @click="loadKnowledge">刷新</el-button>
      </div>
      <div class="knowledge-form">
        <el-input v-model="knowledgeTitle" class="knowledge-title" clearable placeholder="文档标题" />
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".txt,.md,text/plain,text/markdown"
          :on-change="loadKnowledgeFile"
        >
          <el-button plain>读取文本文件</el-button>
        </el-upload>
        <el-button type="primary" :loading="knowledgeUploading" @click="uploadKnowledge">导入文档</el-button>
      </div>
      <el-input
        v-model="knowledgeText"
        type="textarea"
        :rows="4"
        placeholder="粘贴纯文本或 Markdown；导入后按配置切块并立即参与检索"
      />
      <el-table v-loading="knowledgeLoading" :data="knowledge" row-key="id" empty-text="暂无知识文档" class="knowledge-table">
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" width="120" />
        <el-table-column label="更新时间" min-width="170">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" :disabled="acting" @click="deleteKnowledge(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">提醒</h3>
        <el-button :icon="Refresh" :loading="reminderLoading" @click="loadReminders">刷新</el-button>
      </div>
      <div class="tenant-help">待确认的提醒需要用户回复 #确认提醒 &lt;编号&gt; 后才会发送；失败会按 1/2/5/10/30 分钟退避重试。</div>
      <el-table v-loading="reminderLoading" :data="reminders" row-key="id" empty-text="暂无提醒">
        <el-table-column prop="id" label="编号" width="80" />
        <el-table-column prop="user_id" label="用户" width="140" show-overflow-tooltip />
        <el-table-column prop="content" label="内容" min-width="200" show-overflow-tooltip />
        <el-table-column label="计划时间" min-width="170">
          <template #default="{ row }">{{ formatTime(row.due_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="reminderTag(row.status).type" size="small">{{ reminderTag(row.status).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="attempts" label="重试" width="80" />
        <el-table-column label="下次重试" min-width="170">
          <template #default="{ row }">{{ formatTime(row.next_attempt_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_error" label="错误" min-width="160" show-overflow-tooltip />
        <el-table-column label="送达时间" min-width="170">
          <template #default="{ row }">{{ formatTime(row.sent_at) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">后台任务</h3>
        <el-button :icon="Refresh" @click="loadJobs">刷新</el-button>
      </div>
      <el-table :data="jobs.jobs" empty-text="调度器未加载或没有聊天任务">
        <el-table-column prop="id" label="任务" min-width="200" />
        <el-table-column label="下次运行" min-width="170">
          <template #default="{ row }">{{ formatTime(row.next_run) }}</template>
        </el-table-column>
        <el-table-column label="等待中" width="90">
          <template #default="{ row }">{{ row.pending ? '是' : '否' }}</template>
        </el-table-column>
      </el-table>
      <el-table :data="jobs.cursors" empty-text="没有任务游标" class="knowledge-table">
        <el-table-column prop="job" label="游标" width="120" />
        <el-table-column prop="direction" label="方向" width="100" />
        <el-table-column prop="cursor_seq" label="位置" width="100" />
        <el-table-column prop="fail_count" label="失败" width="80" />
        <el-table-column label="下次重试" min-width="170">
          <template #default="{ row }">{{ formatTime(row.next_retry_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_error" label="错误" min-width="200" show-overflow-tooltip />
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">群风格</h3>
        <el-button
          type="danger"
          plain
          :icon="Delete"
          :loading="acting"
          :disabled="!style"
          @click="deleteStyle"
        >
          删除风格
        </el-button>
      </div>
      <template v-if="style">
        <div class="style-summary">
          <el-tag v-if="style.manual_override" size="small" type="warning" class="summary-tag">人工覆盖</el-tag>
          <span>{{ style.manual_override || style.summary || '暂无可注入摘要' }}</span>
        </div>
        <div v-if="style.rules.length" class="style-block">
          <div class="block-label">规则</div>
          <el-tag v-for="rule in style.rules" :key="rule" class="rule-tag" type="info">{{ rule }}</el-tag>
        </div>
        <div v-if="style.examples.length" class="style-block">
          <div class="block-label">示例原话</div>
          <ul class="example-list">
            <li v-for="example in style.examples" :key="`${example.seq}-${example.text}`">
              <span class="example-text">{{ example.text }}</span>
              <span class="example-seq">#{{ example.seq }}</span>
            </li>
          </ul>
        </div>
        <div class="meta-grid">
          <span>版本 {{ style.version }}</span>
          <span>学习 {{ style.learn_count }} 次</span>
          <span>注入 {{ style.use_count }} 次</span>
          <span>证据 {{ style.evidence_count }} 条（#{{ style.evidence_seq_start }}–#{{ style.evidence_seq_end }}）</span>
          <span>最后注入 {{ formatTime(style.last_used_at) }}</span>
          <span>最后学习 {{ formatTime(style.updated_at) }}</span>
        </div>
      </template>
      <el-empty v-else description="尚未学习到群风格。开启配置中的「学习群风格与黑话」后生效。" />
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">群黑话</h3>
        <div class="panel-actions">
          <el-select v-model="jargonStatus" class="status-select" @change="loadJargon">
            <el-option label="全部" value="all" />
            <el-option label="生效中" value="active" />
            <el-option label="已拒绝" value="rejected" />
          </el-select>
          <el-button :icon="Refresh" :loading="jargonLoading" @click="loadJargon">刷新</el-button>
        </div>
      </div>
      <el-table v-loading="jargonLoading" :data="jargon" row-key="id" empty-text="暂无黑话条目">
        <el-table-column prop="term" label="词条" min-width="120" show-overflow-tooltip />
        <el-table-column prop="meaning" label="释义" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
              {{ row.status === 'active' ? '生效' : '已拒绝' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="出现" width="80" />
        <el-table-column prop="learn_count" label="学习" width="80" />
        <el-table-column prop="use_count" label="注入" width="80" />
        <el-table-column label="最近出现" min-width="170">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="最近注入" min-width="170">
          <template #default="{ row }">{{ formatTime(row.last_used_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" :disabled="acting" @click="deleteJargon(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">群表达</h3>
        <div class="panel-actions">
          <el-select v-model="expressionStatus" class="status-select" @change="loadExpressions">
            <el-option label="全部" value="all" />
            <el-option label="生效中" value="active" />
            <el-option label="待复审" value="unchecked" />
            <el-option label="已拒绝" value="rejected" />
          </el-select>
          <el-button :icon="Refresh" :loading="expressionLoading" @click="loadExpressions">刷新</el-button>
        </div>
      </div>
      <el-table v-loading="expressionLoading" :data="expressions" row-key="id" empty-text="暂无表达条目">
        <el-table-column prop="situation" label="情境" min-width="200" show-overflow-tooltip />
        <el-table-column prop="style" label="说法" min-width="200" show-overflow-tooltip />
        <el-table-column prop="type" label="类型" width="90" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="expressionTag(row).type" size="small">{{ expressionTag(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="权重" width="90">
          <template #default="{ row }">{{ formatCount(row.count) }}</template>
        </el-table-column>
        <el-table-column prop="learn_count" label="学习" width="80" />
        <el-table-column prop="use_count" label="注入" width="80" />
        <el-table-column label="最近更新" min-width="170">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="success"
              :disabled="acting || (row.checked && !row.rejected)"
              @click="reviewExpression(row, false)"
            >
              通过
            </el-button>
            <el-button
              link
              type="warning"
              :disabled="acting || (row.checked && row.rejected)"
              @click="reviewExpression(row, true)"
            >
              驳回
            </el-button>
            <el-button link type="danger" :disabled="acting" @click="deleteExpression(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">规划样本</h3>
        <div class="panel-actions">
          <el-button :icon="Refresh" :loading="plannerLoading" @click="loadPlannerSamples">刷新</el-button>
        </div>
      </div>
      <div class="tenant-help">
        Planner 每次调用记录首轮动作；label 为 1 表示决定回复，0 表示保持安静或等待。当前样本：回复
        {{ plannerLabelCounts.replies }} 条，不回复 {{ plannerLabelCounts.silent }} 条。
      </div>
      <el-table v-loading="plannerLoading" :data="plannerSamples" row-key="id" empty-text="暂无规划样本">
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
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">行为样本</h3>
        <div class="panel-actions">
          <el-tag :type="behaviorAttributed >= behaviorMinimum ? 'success' : 'info'" size="small">
            已归因 {{ behaviorAttributed }} / {{ behaviorMinimum }}
          </el-tag>
          <el-button :icon="Refresh" :loading="behaviorLoading" @click="loadBehavior">刷新</el-button>
          <el-button type="danger" plain :icon="Delete" :disabled="!behaviors.length" @click="clearBehavior">
            清空
          </el-button>
        </div>
      </div>
      <div class="tenant-help">
        动作发生后观察 10 分钟，期间有人继续发言记为“续聊”。已归因样本满
        {{ behaviorMinimum }} 条后才把场景→动作注入 Planner；这里只记录，不驱动回复。聚合每周一 04:00 更新。
      </div>
      <el-table v-loading="behaviorLoading" :data="behaviors" empty-text="暂无行为聚合">
        <el-table-column label="场景" min-width="220">
          <template #default="{ row }">{{ row.scene_tags.join(' + ') || '一般' }}</template>
        </el-table-column>
        <el-table-column prop="action" label="动作" width="140" />
        <el-table-column label="续聊率" width="100">
          <template #default="{ row }">{{ Math.round(row.confidence * 100) }}%</template>
        </el-table-column>
        <el-table-column prop="count" label="样本" width="80" />
        <el-table-column label="最近" min-width="170">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="loaded" class="panel">
      <div class="panel-header">
        <h3 class="panel-title">主动接话门控</h3>
        <div class="panel-actions">
          <el-tag :type="replyGate.calibrated ? 'success' : 'warning'" size="small">
            {{ replyGate.calibrated ? '已校准' : '未校准' }}
          </el-tag>
          <el-tag :type="replyGate.proactive.gate_mode === 'enforce' ? 'danger' : 'info'" size="small">
            {{ replyGate.proactive.gate_mode === 'enforce' ? '拦截模式' : '观察模式' }}
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
        <span>全局：{{ replyGate.proactive.enabled ? '开启' : '关闭' }}</span>
        <span>群级开关：{{ proactiveState.switchText }}</span>
        <span>白名单：{{ replyGate.proactive.group_allowed ? '命中' : '未命中' }}</span>
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
        v-loading="gateLoading"
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
          <template #default="{ row }">{{ formatCount(Number(gateFeature(row, 'recent_self_ratio'))) }}</template>
        </el-table-column>
        <el-table-column label="距上次发言" width="110">
          <template #default="{ row }">{{ gateFeature(row, 'seconds_since_bot_message') }}</template>
        </el-table-column>
        <el-table-column label="问句" width="80">
          <template #default="{ row }">{{ gateFeature(row, 'has_question_mark') }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-memory-view {
  padding: 24px;
}

.page-header,
.panel-header,
.tenant-row,
.panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-header,
.panel-header {
  justify-content: space-between;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

.page-header h2,
.panel-title {
  margin: 0;
  color: var(--color-text);
}

.panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 20px;
}

.tenant-panel {
  padding-bottom: 16px;
}

.tenant-label {
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
}

.tenant-select {
  flex: 1;
  max-width: 520px;
}

.tenant-count {
  float: right;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.tenant-help {
  margin-top: 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.style-summary {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  line-height: 1.7;
  color: var(--color-text);
  margin-bottom: 16px;
}

.summary-tag {
  flex-shrink: 0;
}

.style-block {
  margin-bottom: 16px;
}

.block-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}

.rule-tag {
  margin: 0 8px 8px 0;
}

.example-list {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text);
  line-height: 1.8;
}

.example-seq {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.status-select {
  width: 120px;
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

.gate-distribution .block-label {
  margin-bottom: 0;
}

.emoji-description {
  width: 240px;
}

.emoji-preview {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--color-border);
}

.knowledge-form {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}

.knowledge-title {
  width: 320px;
}

.knowledge-table {
  margin-top: 16px;
}
</style>
