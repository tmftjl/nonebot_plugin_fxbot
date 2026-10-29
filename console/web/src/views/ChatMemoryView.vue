<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Delete, Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatScopeList from '@/components/ChatScopeList.vue'
import { chatApi } from '@/api'
import type {
  ChatTenant,
  ChatStyle,
  ChatJargon,
  ChatExpression,
  ChatEmoji,
  ChatEmojiCounts,
} from '@/types/api'

const TAB_KEY = 'fxbot-chat-learning-tab'
const LEGACY_TABS: Record<string, string> = { learning: 'expression', assets: 'emoji' }

const tabs = [
  { key: 'expression', label: '表达方式', hint: '情境与说法' },
  { key: 'jargon', label: '黑话', hint: '群内词条与释义' },
  { key: 'emoji', label: '表情包', hint: '已收集与已注册表情' },
] as const

type TabKey = (typeof tabs)[number]['key']
const savedTab = LEGACY_TABS[localStorage.getItem(TAB_KEY) || ''] || localStorage.getItem(TAB_KEY)
const activeTab = ref<TabKey>(
  tabs.some((tab) => tab.key === savedTab) ? (savedTab as TabKey) : 'expression',
)

watch(activeTab, (value) => localStorage.setItem(TAB_KEY, value))

const tenants = ref<ChatTenant[]>([])
const tenant = ref('')
const loading = ref(false)
const acting = ref(false)

const expressions = ref<ChatExpression[]>([])
const expressionStatus = ref('all')
const expressionSearch = ref('')
const expressionLoading = ref(false)
const expressionSelection = ref<number[]>([])

const jargon = ref<ChatJargon[]>([])
const jargonStatus = ref('all')
const jargonSearch = ref('')
const jargonLoading = ref(false)
const jargonSelection = ref<number[]>([])

const emojis = ref<ChatEmoji[]>([])
const emojiLoading = ref(false)
const emojiUploading = ref(false)
const emojiDescription = ref('')
const emojiStatus = ref('')
const emojiSearch = ref('')
const emojiFormat = ref('')
const emojiSort = ref('newest')
const emojiSelection = ref<string[]>([])
const emojiCounts = ref<ChatEmojiCounts>({
  registered: 0,
  pending: 0,
  unidentified: 0,
  disabled: 0,
  all: 0,
})

const style = ref<ChatStyle | null>(null)
const styleLoading = ref(false)
const styleDrawer = ref(false)
const styleOverride = ref('')
const styleSaving = ref(false)

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

const filteredExpressions = computed(() => {
  const keyword = expressionSearch.value.trim().toLowerCase()
  if (!keyword) return expressions.value
  return expressions.value.filter((row) =>
    [row.situation, row.style, row.type].some((value) => String(value || '').toLowerCase().includes(keyword)),
  )
})

const filteredJargon = computed(() => {
  const keyword = jargonSearch.value.trim().toLowerCase()
  if (!keyword) return jargon.value
  return jargon.value.filter((row) =>
    [row.term, row.meaning].some((value) => String(value || '').toLowerCase().includes(keyword)),
  )
})

const emojiFilters = computed(() => [
  { key: '', label: '全部', count: emojiCounts.value.all },
  { key: 'registered', label: '已注册', count: emojiCounts.value.registered },
  { key: 'pending', label: '待审核 · 已识别', count: emojiCounts.value.pending },
  { key: 'unidentified', label: '待审核 · 未识别', count: emojiCounts.value.unidentified },
  { key: 'disabled', label: '已禁用', count: emojiCounts.value.disabled },
])

const emojiFormats = [
  { key: '', label: '全部' },
  { key: 'png', label: 'PNG' },
  { key: 'jpg', label: 'JPG' },
  { key: 'gif', label: 'GIF' },
  { key: 'webp', label: 'WEBP' },
  { key: 'bmp', label: 'BMP' },
]

const emojiStatusText = (row: ChatEmoji) => {
  switch (row.status) {
    case 'registered':
      return '已注册'
    case 'pending':
      return '待审核 · 已识别'
    case 'unidentified':
      return '待审核 · 未识别'
    default:
      return '已禁用'
  }
}

const emojiTagType = (row: ChatEmoji): 'success' | 'warning' | 'info' | 'danger' => {
  switch (row.status) {
    case 'registered':
      return 'success'
    case 'pending':
      return 'warning'
    case 'unidentified':
      return 'info'
    default:
      return 'danger'
  }
}

const emojiSortOptions: Record<string, { sort: string; order: string }> = {
  newest: { sort: 'created_at', order: 'desc' },
  oldest: { sort: 'created_at', order: 'asc' },
  count: { sort: 'count', order: 'desc' },
  last_used: { sort: 'last_used', order: 'desc' },
  register_time: { sort: 'register_time', order: 'desc' },
}

const loadTenants = async () => {
  try {
    const result = await chatApi.tenants()
    tenants.value = result.tenants || []
    if (tenant.value && !tenants.value.some((item) => item.tenant === tenant.value)) tenant.value = ''
    if (!tenant.value && tenants.value.length) tenant.value = tenants.value[0].tenant
  } catch (error) {
    console.error('加载聊天租户失败', error)
    ElMessage.error('加载聊天租户失败，请重试')
  }
}

const loadExpressions = async () => {
  if (!tenant.value) {
    expressions.value = []
    expressionSelection.value = []
    return
  }
  expressionLoading.value = true
  try {
    const result = await chatApi.listExpressions(tenant.value, expressionStatus.value)
    expressions.value = result.items || []
    expressionSelection.value = []
  } catch (error) {
    console.error('加载群表达失败', error)
    ElMessage.error('加载群表达失败，请重试')
  } finally {
    expressionLoading.value = false
  }
}

const loadJargon = async () => {
  if (!tenant.value) {
    jargon.value = []
    jargonSelection.value = []
    return
  }
  jargonLoading.value = true
  try {
    const result = await chatApi.listJargon(tenant.value, jargonStatus.value)
    jargon.value = result.items || []
    jargonSelection.value = []
  } catch (error) {
    console.error('加载黑话失败', error)
    ElMessage.error('加载黑话失败，请重试')
  } finally {
    jargonLoading.value = false
  }
}

const loadEmoji = async () => {
  emojiSelection.value = []
  if (!tenant.value) {
    emojis.value = []
    return
  }
  emojiLoading.value = true
  const ordering = emojiSortOptions[emojiSort.value] || emojiSortOptions.newest
  try {
    const result = await chatApi.listEmoji(tenant.value, {
      status: emojiStatus.value || undefined,
      search: emojiSearch.value.trim() || undefined,
      format: emojiFormat.value || undefined,
      sort: ordering.sort,
      order: ordering.order,
    })
    emojis.value = result.items || []
    emojiCounts.value = result.counts || emojiCounts.value
  } catch (error) {
    console.error('加载表情失败', error)
    ElMessage.error('加载表情失败，请重试')
  } finally {
    emojiLoading.value = false
  }
}

const loadStyle = async () => {
  if (!tenant.value) {
    style.value = null
    return
  }
  styleLoading.value = true
  try {
    const result = await chatApi.getStyle(tenant.value)
    style.value = result.style
    styleOverride.value = result.style?.manual_override || ''
  } catch (error) {
    console.error('加载群风格失败', error)
    ElMessage.error('加载群风格失败，请重试')
  } finally {
    styleLoading.value = false
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    await Promise.all([loadExpressions(), loadJargon(), loadEmoji(), loadStyle()])
  } finally {
    loading.value = false
  }
}

const onTenantChange = async () => {
  expressionSelection.value = []
  jargonSelection.value = []
  await loadAll()
}

const refresh = async () => {
  if (loading.value) return
  await loadTenants()
  await loadAll()
}

const onExpressionSelection = (rows: ChatExpression[]) => {
  expressionSelection.value = rows.map((row) => row.id)
}

const onJargonSelection = (rows: ChatJargon[]) => {
  jargonSelection.value = rows.map((row) => row.id)
}

const batchReview = async (rejected: boolean) => {
  if (acting.value || !expressionSelection.value.length) return
  acting.value = true
  const ids = [...expressionSelection.value]
  let failed = 0
  for (const id of ids) {
    try {
      await chatApi.reviewExpression(tenant.value, id, rejected)
    } catch (error) {
      console.error('批量改判表达失败', error)
      failed += 1
    }
  }
  acting.value = false
  if (failed) ElMessage.warning(`${ids.length - failed} 条已更新，${failed} 条失败`)
  else ElMessage.success(rejected ? `已驳回 ${ids.length} 条表达式` : `已通过 ${ids.length} 条表达式`)
  await loadExpressions()
}

const deleteSelected = async (kind: 'expression' | 'jargon') => {
  if (acting.value) return
  const ids = kind === 'expression' ? [...expressionSelection.value] : [...jargonSelection.value]
  if (!ids.length) return
  const label = kind === 'expression' ? '表达方式' : '黑话'
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${ids.length} 条${label}？`, `批量删除${label}`, {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  acting.value = true
  let failed = 0
  for (const id of ids) {
    try {
      if (kind === 'expression') await chatApi.deleteExpression(tenant.value, id)
      else await chatApi.deleteJargon(tenant.value, id)
    } catch (error) {
      console.error(`批量删除${label}失败`, error)
      failed += 1
    }
  }
  acting.value = false
  if (failed) ElMessage.warning(`${ids.length - failed} 条已删除，${failed} 条失败`)
  else ElMessage.success(`已删除 ${ids.length} 条${label}`)
  if (kind === 'expression') await loadExpressions()
  else await loadJargon()
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
    await ElMessageBox.confirm(`确定删除表达「${row.style}」？删除后不再注入这条情境说法。`, '删除表达', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
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

const deleteJargon = async (row: ChatJargon) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(`确定删除黑话「${row.term}」？删除后当前消息不再注入该释义。`, '删除黑话', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
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

const setEmojiStatus = async (status: string) => {
  emojiStatus.value = status
  await loadEmoji()
}

const setEmojiFormat = async (format: string) => {
  emojiFormat.value = format
  await loadEmoji()
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

const patchEmoji = async (
  row: ChatEmoji,
  patch: { enabled?: boolean; registered?: boolean; description?: string; tags?: string[] },
  message: string,
) => {
  if (acting.value) return
  acting.value = true
  try {
    await chatApi.updateEmoji(tenant.value, row.hash, patch)
    ElMessage.success(message)
    await loadEmoji()
  } catch (error) {
    console.error('更新表情失败', error)
    ElMessage.error('更新表情失败，请重试')
  } finally {
    acting.value = false
  }
}

const toggleEmoji = (row: ChatEmoji) =>
  patchEmoji(row, { enabled: !row.enabled }, row.enabled ? '表情已禁用' : '表情已启用')

const registerEmoji = (row: ChatEmoji) =>
  patchEmoji(row, { registered: !row.registered }, row.registered ? '已取消注册' : '表情已注册')

const editEmoji = async (row: ChatEmoji) => {
  let value = ''
  try {
    const result = await ElMessageBox.prompt('修改表情描述，用于匹配回复内容', '编辑表情', {
      inputValue: row.description,
      inputType: 'textarea',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    value = result.value
  } catch {
    return
  }
  await patchEmoji(row, { description: value.trim() }, '描述已更新')
}

const deleteEmoji = async (row: ChatEmoji) => {
  if (acting.value) return
  try {
    await ElMessageBox.confirm('确定删除这条表情？文件与索引会一起移除。', '删除表情', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
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

const toggleEmojiSelection = (hash: string) => {
  const index = emojiSelection.value.indexOf(hash)
  if (index >= 0) emojiSelection.value.splice(index, 1)
  else emojiSelection.value.push(hash)
}

const deleteSelectedEmoji = async () => {
  if (acting.value || !emojiSelection.value.length) return
  const hashes = [...emojiSelection.value]
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${hashes.length} 个表情？文件与索引会一起移除。`, '批量删除表情', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  acting.value = true
  try {
    const result = await chatApi.batchDeleteEmoji(tenant.value, hashes)
    ElMessage.success(`已删除 ${result.deleted} 个表情`)
    await loadEmoji()
  } catch (error) {
    console.error('批量删除表情失败', error)
    ElMessage.error('批量删除表情失败，请重试')
  } finally {
    acting.value = false
  }
}

const saveStyle = async () => {
  if (!tenant.value || styleSaving.value) return
  styleSaving.value = true
  try {
    const result = await chatApi.saveStyle(tenant.value, styleOverride.value.trim())
    style.value = result.style
    styleOverride.value = result.style.manual_override
    ElMessage.success(result.style.manual_override ? '人工覆盖已保存' : '人工覆盖已取消')
  } catch (error) {
    console.error('保存风格覆盖失败', error)
    ElMessage.error('保存风格覆盖失败，请重试')
  } finally {
    styleSaving.value = false
  }
}

const deleteStyle = async () => {
  if (!style.value || acting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除租户「${tenant.value}」的群风格？删除后立即停止注入，下次学习会重新生成。`,
      '删除群风格',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  acting.value = true
  try {
    await chatApi.deleteStyle(tenant.value)
    ElMessage.success('群风格已删除')
    style.value = null
    styleOverride.value = ''
  } catch (error) {
    console.error('删除群风格失败', error)
    ElMessage.error('删除群风格失败，请重试')
  } finally {
    acting.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="chat-learning-view">
    <PageHeader title="学习" subtitle="按会话查看表达方式、黑话与表情包；每个会话独立学习">
      <template #actions>
        <el-button @click="styleDrawer = true">群风格</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <div class="memory-layout">
      <ChatScopeList v-model="tenant" :tenants="tenants" show-stats @change="onTenantChange" />

      <section class="memory-main">
        <div class="main-head">
          <PillTabs v-model="activeTab" :items="tabs" />
          <div class="scope-current">
            当前 <code class="tenant-id">{{ tenant || '未选择' }}</code>
          </div>
        </div>

        <Transition name="tab-swap" mode="out-in">
          <div :key="activeTab" class="tab-stack">
            <div v-if="activeTab === 'expression'" class="panel">
              <div class="panel-header">
                <div class="panel-heading">
                  <h3 class="panel-title">表达方式</h3>
                  <span class="panel-count">{{ filteredExpressions.length }} 条</span>
                </div>
                <div class="filters">
                  <el-select v-model="expressionStatus" class="status-select" @change="loadExpressions">
                    <el-option label="全部" value="all" />
                    <el-option label="生效中" value="active" />
                    <el-option label="待复审" value="unchecked" />
                    <el-option label="已拒绝" value="rejected" />
                  </el-select>
                  <el-input
                    v-model="expressionSearch"
                    class="search-input"
                    clearable
                    placeholder="搜索情境、说法或类型"
                    :prefix-icon="Search"
                  />
                  <el-button :icon="Refresh" :loading="expressionLoading" @click="loadExpressions">刷新</el-button>
                </div>
              </div>
              <div class="batch-bar">
                <span class="batch-info">已选 {{ expressionSelection.length }} 条</span>
                <div class="batch-actions">
                  <el-button
                    size="small"
                    :disabled="!expressionSelection.length || acting"
                    @click="batchReview(false)"
                  >
                    通过
                  </el-button>
                  <el-button
                    size="small"
                    :disabled="!expressionSelection.length || acting"
                    @click="batchReview(true)"
                  >
                    驳回
                  </el-button>
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :icon="Delete"
                    :disabled="!expressionSelection.length || acting"
                    @click="deleteSelected('expression')"
                  >
                    删除
                  </el-button>
                </div>
              </div>
              <el-table
                :data="filteredExpressions"
                row-key="id"
                empty-text="暂无表达方式"
                @selection-change="onExpressionSelection"
              >
                <el-table-column type="selection" width="44" />
                <el-table-column prop="situation" label="情境" min-width="180" show-overflow-tooltip />
                <el-table-column prop="style" label="说法" min-width="180" show-overflow-tooltip />
                <el-table-column prop="type" label="类型" width="90" />
                <el-table-column label="状态" width="96">
                  <template #default="{ row }">
                    <el-tag :type="expressionTag(row).type" size="small">{{ expressionTag(row).text }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="权重" width="84">
                  <template #default="{ row }">{{ formatCount(row.count) }}</template>
                </el-table-column>
                <el-table-column prop="learn_count" label="学习" width="76" />
                <el-table-column prop="use_count" label="使用" width="76" />
                <el-table-column label="最近更新" width="168">
                  <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="170" fixed="right">
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

            <div v-if="activeTab === 'jargon'" class="panel">
              <div class="panel-header">
                <div class="panel-heading">
                  <h3 class="panel-title">黑话</h3>
                  <span class="panel-count">{{ filteredJargon.length }} 条</span>
                </div>
                <div class="filters">
                  <el-select v-model="jargonStatus" class="status-select" @change="loadJargon">
                    <el-option label="全部" value="all" />
                    <el-option label="生效中" value="active" />
                    <el-option label="已拒绝" value="rejected" />
                  </el-select>
                  <el-input
                    v-model="jargonSearch"
                    class="search-input"
                    clearable
                    placeholder="搜索词条或释义"
                    :prefix-icon="Search"
                  />
                  <el-button :icon="Refresh" :loading="jargonLoading" @click="loadJargon">刷新</el-button>
                </div>
              </div>
              <div class="batch-bar">
                <span class="batch-info">已选 {{ jargonSelection.length }} 条</span>
                <div class="batch-actions">
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :icon="Delete"
                    :disabled="!jargonSelection.length || acting"
                    @click="deleteSelected('jargon')"
                  >
                    删除
                  </el-button>
                </div>
              </div>
              <el-table
                :data="filteredJargon"
                row-key="id"
                empty-text="暂无黑话条目"
                @selection-change="onJargonSelection"
              >
                <el-table-column type="selection" width="44" />
                <el-table-column prop="term" label="词条" min-width="130" show-overflow-tooltip />
                <el-table-column prop="meaning" label="释义" min-width="240" show-overflow-tooltip />
                <el-table-column label="状态" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
                      {{ row.status === 'active' ? '生效' : '已拒绝' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="count" label="出现" width="80" />
                <el-table-column prop="learn_count" label="学习" width="76" />
                <el-table-column prop="use_count" label="使用" width="76" />
                <el-table-column label="最近出现" width="168">
                  <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
                </el-table-column>
                <el-table-column label="最近使用" width="168">
                  <template #default="{ row }">{{ formatTime(row.last_used_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="danger" :disabled="acting" @click="deleteJargon(row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-if="activeTab === 'emoji'" class="panel">
              <div class="emoji-layout">
                <aside class="emoji-side">
                  <div class="side-section">
                    <div class="side-title">状态</div>
                    <button
                      v-for="filter in emojiFilters"
                      :key="filter.key"
                      type="button"
                      class="side-item"
                      :class="{ active: emojiStatus === filter.key }"
                      @click="setEmojiStatus(filter.key)"
                    >
                      <span>{{ filter.label }}</span>
                      <span class="side-count">{{ filter.count }}</span>
                    </button>
                  </div>
                  <div class="side-section">
                    <div class="side-title">格式</div>
                    <div class="format-grid">
                      <button
                        v-for="format in emojiFormats"
                        :key="format.key"
                        type="button"
                        class="format-item"
                        :class="{ active: emojiFormat === format.key }"
                        @click="setEmojiFormat(format.key)"
                      >
                        {{ format.label }}
                      </button>
                    </div>
                  </div>
                  <div class="side-section">
                    <div class="side-title">排序</div>
                    <el-select v-model="emojiSort" class="side-select" @change="loadEmoji">
                      <el-option label="最近加入" value="newest" />
                      <el-option label="最早加入" value="oldest" />
                      <el-option label="发送最多" value="count" />
                      <el-option label="最近使用" value="last_used" />
                      <el-option label="注册时间" value="register_time" />
                    </el-select>
                  </div>
                </aside>

                <section class="emoji-main">
                  <div class="emoji-toolbar">
                    <el-input
                      v-model="emojiSearch"
                      class="search-input"
                      clearable
                      placeholder="搜索描述或标签"
                      :prefix-icon="Search"
                      @keyup.enter="loadEmoji"
                      @clear="loadEmoji"
                    />
                    <div class="toolbar-right">
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
                  <div class="batch-bar">
                    <span class="batch-info">已选 {{ emojiSelection.length }} 个</span>
                    <div class="batch-actions">
                      <el-button
                        size="small"
                        type="danger"
                        plain
                        :icon="Delete"
                        :disabled="!emojiSelection.length || acting"
                        @click="deleteSelectedEmoji"
                      >
                        删除
                      </el-button>
                    </div>
                  </div>
                  <div class="side-hint">
                    聊天中收集的表情会自动识别并在名额内注册；已注册表情按描述匹配回复，同一表情冷却 5 分钟。
                  </div>
                  <div class="emoji-grid">
                    <div v-for="row in emojis" :key="row.hash" class="emoji-card">
                      <div class="emoji-card-image-wrap">
                        <img
                          class="emoji-card-image"
                          :src="chatApi.emojiThumbnailUrl(tenant, row.hash)"
                          :alt="row.description"
                        >
                        <el-checkbox
                          class="emoji-card-check"
                          :model-value="emojiSelection.includes(row.hash)"
                          @change="toggleEmojiSelection(row.hash)"
                        />
                      </div>
                      <div class="emoji-card-body">
                        <div class="emoji-card-desc">{{ row.description || '暂无描述' }}</div>
                        <div class="emoji-card-tags">
                          <el-tag
                            v-for="tag in row.tags"
                            :key="tag"
                            size="small"
                            type="info"
                            class="rule-tag"
                          >
                            {{ tag }}
                          </el-tag>
                        </div>
                        <div class="emoji-card-meta">
                          <el-tag size="small" :type="emojiTagType(row)">{{ emojiStatusText(row) }}</el-tag>
                          <span>发送 {{ row.count }} 次</span>
                          <span>{{ formatTime(row.last_used) }}</span>
                        </div>
                      </div>
                      <div class="emoji-card-actions">
                        <el-button link :disabled="acting" @click="toggleEmoji(row)">
                          {{ row.enabled ? '禁用' : '启用' }}
                        </el-button>
                        <el-button link type="primary" :disabled="acting" @click="registerEmoji(row)">
                          {{ row.registered ? '取消注册' : '注册' }}
                        </el-button>
                        <el-button link :disabled="acting" @click="editEmoji(row)">编辑</el-button>
                        <el-button link type="danger" :disabled="acting" @click="deleteEmoji(row)">删除</el-button>
                      </div>
                    </div>
                  </div>
                  <el-empty v-if="!emojiLoading && !emojis.length" description="暂无表情" />
                </section>
              </div>
            </div>
          </div>
        </Transition>
      </section>
    </div>

    <el-drawer v-model="styleDrawer" title="群风格" size="560px">
      <div class="drawer-body">
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
            <span>使用 {{ style.use_count }} 次</span>
            <span>证据 {{ style.evidence_count }} 条（#{{ style.evidence_seq_start }}–#{{ style.evidence_seq_end }}）</span>
            <span>最后使用 {{ formatTime(style.last_used_at) }}</span>
            <span>最后学习 {{ formatTime(style.updated_at) }}</span>
          </div>
          <div class="drawer-actions">
            <el-input
              v-model="styleOverride"
              class="override-input"
              placeholder="人工覆盖摘要，留空取消"
              clearable
              maxlength="200"
            />
            <el-button type="primary" :loading="styleSaving" @click="saveStyle">保存覆盖</el-button>
            <el-button type="danger" plain :icon="Delete" :loading="acting" @click="deleteStyle">删除风格</el-button>
          </div>
        </template>
        <el-empty v-else description="尚未学习到群风格。开启配置中的「学习群风格与黑话」后生效。" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.chat-learning-view {
  padding: 24px 28px 32px;
}

.memory-layout {
  display: grid;
  grid-template-columns: 272px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.memory-main {
  min-width: 0;
}

.main-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.scope-current {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.tenant-id {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  word-break: break-all;
}

.tab-stack {
  display: flex;
  flex-direction: column;
}

.tab-swap-enter-active,
.tab-swap-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
}

.tab-swap-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.tab-swap-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .tab-swap-enter-active,
  .tab-swap-leave-active {
    transition: none;
  }
}

.panel-header,
.filters,
.batch-bar,
.batch-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.panel-header {
  justify-content: space-between;
  flex-wrap: wrap;
}

.panel-heading {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.panel-count {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.filters {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.status-select {
  width: 116px;
}

.search-input {
  width: 240px;
}

.batch-bar {
  justify-content: space-between;
  margin: 14px 0 10px;
  padding: 8px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
}

.batch-info {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.batch-actions {
  gap: 6px;
}

.emoji-layout {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 20px;
}

.emoji-side {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-right: 16px;
  border-right: 1px solid var(--color-border-light);
}

.side-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.side-title {
  margin-bottom: 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 600;
}

.side-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }

  &.active {
    background: var(--color-primary-soft);
    color: var(--color-primary);
    font-weight: 600;
  }
}

.side-count {
  flex: none;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.format-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.format-item {
  padding: 4px 9px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;

  &:hover {
    border-color: var(--color-text-tertiary);
  }

  &.active {
    border-color: var(--color-primary);
    background: var(--color-primary-soft);
    color: var(--color-primary);
    font-weight: 600;
  }
}

.side-select {
  width: 100%;
}

.emoji-main {
  min-width: 0;
}

.emoji-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.toolbar-right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.emoji-description {
  width: 230px;
}

.side-hint {
  margin: 10px 0 14px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.emoji-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 16px;
  min-height: 80px;
}

.emoji-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-card);
}

.emoji-card-image-wrap {
  position: relative;
}

.emoji-card-image {
  width: 100%;
  height: 140px;
  object-fit: contain;
  background: var(--color-bg-secondary);
}

.emoji-card-check {
  position: absolute;
  top: 6px;
  left: 8px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--color-bg-card);
}

.emoji-card-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px 0;
}

.emoji-card-desc {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.emoji-card-tags {
  display: flex;
  flex-wrap: wrap;
}

.emoji-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.emoji-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px;
  border-top: 1px solid var(--color-border);
}

.rule-tag {
  margin: 0 8px 8px 0;
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.style-summary {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--color-text);
  line-height: 1.7;
}

.summary-tag {
  flex-shrink: 0;
}

.block-label {
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.example-list {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text);
  line-height: 1.8;
}

.example-seq {
  margin-left: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border-light);
}

.override-input {
  flex: 1;
  min-width: 180px;
}

@media (max-width: 1100px) {
  .memory-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .chat-learning-view {
    padding: 16px;
  }

  .emoji-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .emoji-side {
    padding-right: 0;
    padding-bottom: 16px;
    border-right: 0;
    border-bottom: 1px solid var(--color-border-light);
  }

  .search-input,
  .emoji-description {
    width: 100%;
  }
}
</style>
