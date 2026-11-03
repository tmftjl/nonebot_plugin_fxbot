<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import {
  Close,
  Delete,
  EditPen,
  Hide,
  Plus,
  Refresh,
  Search,
  Select,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatScopeList from '@/components/ChatScopeList.vue'
import ListPager from '@/components/ListPager.vue'
import { chatApi } from '@/api'
import type {
  ChatTenant,
  ChatStyle,
  ChatJargon,
  ChatExpression,
  ChatExpressionCandidate,
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
const expressionDialogVisible = ref(false)
const expressionSaving = ref(false)
const expressionForm = ref({ situation: '', style: '' })
const learnDialogVisible = ref(false)
const learnText = ref('')
const learnBusy = ref(false)
const learnImporting = ref(false)
const learnTotal = ref(0)
const learnDone = ref(0)
const learnCandidates = ref<Array<ChatExpressionCandidate & { selected: boolean }>>([])
const learnFailed = ref<string[]>([])

const jargon = ref<ChatJargon[]>([])
const jargonStatus = ref('all')
const jargonSearch = ref('')
const jargonLoading = ref(false)
const jargonSelection = ref<number[]>([])
const jargonDialogVisible = ref(false)
const jargonSaving = ref(false)
const jargonForm = ref({ term: '', meaning: '' })

const emojis = ref<ChatEmoji[]>([])
const emojiLoading = ref(false)
const emojiUploading = ref(false)
const emojiDescription = ref('')
const emojiStatus = ref('')
const emojiSearch = ref('')
const emojiFormat = ref('')
const emojiSort = ref('newest')
const emojiCardSize = ref<'small' | 'medium' | 'large'>('medium')
const emojiSelection = ref<string[]>([])
const emojiDetailVisible = ref(false)
const emojiDetailRow = ref<ChatEmoji | null>(null)
const emojiUploadVisible = ref(false)
const emojiUploadFiles = ref<UploadUserFile[]>([])
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

const jargonTag = (status: string): { type: 'success' | 'warning' | 'info'; text: string } => {
  if (status === 'active') return { type: 'success', text: '生效' }
  if (status === 'pending') return { type: 'warning', text: '待推断' }
  return { type: 'info', text: '已拒绝' }
}

const jargonMeaning = (row: ChatJargon): string => {
  if (row.meaning) return row.meaning
  if (row.status !== 'pending') return '未推断出释义'
  const threshold = [4, 8, 25, 100].find((value) => value > row.count)
  return threshold ? `出现 ${threshold} 次后推断释义（当前 ${row.count} 次）` : '重复出现后推断释义'
}

const EXPRESSION_PAGE_SIZE = 20
const JARGON_PAGE_SIZE = 20
const EMOJI_PAGE_SIZE = 24
const expressionPage = ref(1)
const expressionTotal = ref(0)
const jargonPage = ref(1)
const jargonTotal = ref(0)
const emojiPage = ref(1)
const emojiTotal = ref(0)

let expressionSearchTimer: number | null = null
let jargonSearchTimer: number | null = null

const reloadExpressions = () => {
  if (expressionPage.value === 1) void loadExpressions()
  else expressionPage.value = 1
}

const reloadJargon = () => {
  if (jargonPage.value === 1) void loadJargon()
  else jargonPage.value = 1
}

const reloadEmoji = () => {
  if (emojiPage.value === 1) void loadEmoji()
  else emojiPage.value = 1
}

const onExpressionSearch = () => {
  if (expressionSearchTimer !== null) window.clearTimeout(expressionSearchTimer)
  expressionSearchTimer = window.setTimeout(() => {
    expressionSearchTimer = null
    reloadExpressions()
  }, 300)
}

const onJargonSearch = () => {
  if (jargonSearchTimer !== null) window.clearTimeout(jargonSearchTimer)
  jargonSearchTimer = window.setTimeout(() => {
    jargonSearchTimer = null
    reloadJargon()
  }, 300)
}

watch(expressionPage, () => void loadExpressions())
watch(jargonPage, () => void loadJargon())
watch(emojiPage, () => void loadEmoji())

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

const emojiCardSizes = [
  { key: 'small', label: '小卡片', dot: '6px' },
  { key: 'medium', label: '中卡片', dot: '10px' },
  { key: 'large', label: '大卡片', dot: '14px' },
] as const

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
  expressionSelection.value = []
  if (!tenant.value) {
    expressions.value = []
    expressionTotal.value = 0
    return
  }
  expressionLoading.value = true
  try {
    const result = await chatApi.listExpressions(tenant.value, {
      status: expressionStatus.value,
      search: expressionSearch.value.trim() || undefined,
      limit: EXPRESSION_PAGE_SIZE,
      offset: (expressionPage.value - 1) * EXPRESSION_PAGE_SIZE,
    })
    expressions.value = result.items || []
    expressionTotal.value = result.total || 0
    const pages = Math.max(1, Math.ceil(expressionTotal.value / EXPRESSION_PAGE_SIZE))
    if (expressionPage.value > pages) {
      expressionPage.value = pages
      return
    }
  } catch (error) {
    console.error('加载群表达失败', error)
    ElMessage.error('加载群表达失败，请重试')
  } finally {
    expressionLoading.value = false
  }
}

const loadJargon = async () => {
  jargonSelection.value = []
  if (!tenant.value) {
    jargon.value = []
    jargonTotal.value = 0
    return
  }
  jargonLoading.value = true
  try {
    const result = await chatApi.listJargon(tenant.value, {
      status: jargonStatus.value,
      search: jargonSearch.value.trim() || undefined,
      limit: JARGON_PAGE_SIZE,
      offset: (jargonPage.value - 1) * JARGON_PAGE_SIZE,
    })
    jargon.value = result.items || []
    jargonTotal.value = result.total || 0
    const pages = Math.max(1, Math.ceil(jargonTotal.value / JARGON_PAGE_SIZE))
    if (jargonPage.value > pages) {
      jargonPage.value = pages
      return
    }
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
    emojiTotal.value = 0
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
      limit: EMOJI_PAGE_SIZE,
      offset: (emojiPage.value - 1) * EMOJI_PAGE_SIZE,
    })
    emojis.value = result.items || []
    emojiTotal.value = result.total || 0
    const pages = Math.max(1, Math.ceil(emojiTotal.value / EMOJI_PAGE_SIZE))
    if (emojiPage.value > pages) {
      emojiPage.value = pages
      return
    }
    emojiCounts.value = result.counts || emojiCounts.value
    if (emojiDetailVisible.value && emojiDetailRow.value) {
      const fresh = emojis.value.find((row) => row.hash === emojiDetailRow.value?.hash)
      if (fresh) emojiDetailRow.value = fresh
      else emojiDetailVisible.value = false
    }
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
  expressionPage.value = 1
  jargonPage.value = 1
  emojiPage.value = 1
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

const selectedCandidateCount = computed(() => learnCandidates.value.filter((item) => item.selected).length)

const openExpressionDialog = () => {
  expressionForm.value = { situation: '', style: '' }
  expressionDialogVisible.value = true
}

const saveExpression = async () => {
  if (!tenant.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  const situation = expressionForm.value.situation.trim()
  const style = expressionForm.value.style.trim()
  if (!situation || !style) {
    ElMessage.warning('请填写情境和说法')
    return
  }
  if (expressionSaving.value) return
  expressionSaving.value = true
  try {
    await chatApi.createExpression(tenant.value, situation, style)
    expressionDialogVisible.value = false
    ElMessage.success('已保存表达方式')
    await loadExpressions()
  } catch (error: any) {
    console.error('保存表达失败', error)
    ElMessage.error(error?.response?.data?.detail || '保存表达失败，请重试')
  } finally {
    expressionSaving.value = false
  }
}

const resetLearned = () => {
  learnText.value = ''
  learnCandidates.value = []
  learnFailed.value = []
  learnTotal.value = 0
  learnDone.value = 0
}

const openLearnDialog = () => {
  resetLearned()
  learnDialogVisible.value = true
}

const extractChunks = async (chunks: string[]) => {
  learnTotal.value = chunks.length
  learnDone.value = 0
  const seen = new Set(learnCandidates.value.map((item) => `${item.situation}\n${item.style}`))
  for (let index = 0; index < chunks.length; index += 1) {
    try {
      const result = await chatApi.extractExpressionLearning(tenant.value, chunks[index])
      for (const candidate of result.candidates || []) {
        const key = `${candidate.situation}\n${candidate.style}`
        if (seen.has(key)) continue
        seen.add(key)
        learnCandidates.value.push({ ...candidate, selected: true })
      }
    } catch (error) {
      console.error(`第 ${index + 1} 段提取失败`, error)
      learnFailed.value.push(chunks[index])
    } finally {
      learnDone.value = index + 1
    }
  }
}

const learnFromText = async () => {
  if (!tenant.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  const text = learnText.value.trim()
  if (!text) {
    ElMessage.warning('请先粘贴要学习的文本')
    return
  }
  if (learnBusy.value) return
  learnBusy.value = true
  learnCandidates.value = []
  learnFailed.value = []
  learnTotal.value = 0
  learnDone.value = 0
  try {
    const prepared = await chatApi.prepareExpressionLearning(text)
    await extractChunks(prepared.chunks || [])
    if (learnCandidates.value.length) ElMessage.success(`已提取 ${learnCandidates.value.length} 条候选`)
    else ElMessage.warning('没有提取到可用表达')
  } catch (error: any) {
    console.error('准备学习文段失败', error)
    ElMessage.error(error?.response?.data?.detail || '文本学习失败，请重试')
  } finally {
    learnBusy.value = false
  }
}

const retryFailedChunks = async () => {
  if (learnBusy.value || !learnFailed.value.length) return
  const chunks = [...learnFailed.value]
  learnFailed.value = []
  learnBusy.value = true
  try {
    await extractChunks(chunks)
  } finally {
    learnBusy.value = false
  }
}

const importLearnedExpressions = async () => {
  if (!tenant.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  const selected = learnCandidates.value.filter((item) => item.selected)
  if (!selected.length) {
    ElMessage.warning('请选择要导入的候选')
    return
  }
  const items = selected.map((item) => ({
    situation: item.situation.trim(),
    style: item.style.trim(),
  }))
  if (items.some((item) => !item.situation || !item.style)) {
    ElMessage.warning('候选的情境和说法都不能为空')
    return
  }
  if (learnImporting.value) return
  learnImporting.value = true
  try {
    const result = await chatApi.importExpressions(tenant.value, items)
    ElMessage.success(`已导入 ${result.imported} 条表达`)
    learnDialogVisible.value = false
    await loadExpressions()
  } catch (error: any) {
    console.error('导入表达失败', error)
    ElMessage.error(error?.response?.data?.detail || '导入表达失败，请重试')
  } finally {
    learnImporting.value = false
  }
}

const openJargonDialog = () => {
  jargonForm.value = { term: '', meaning: '' }
  jargonDialogVisible.value = true
}

const saveJargon = async () => {
  if (!tenant.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  const term = jargonForm.value.term.trim()
  const meaning = jargonForm.value.meaning.trim()
  if (term.length < 2) {
    ElMessage.warning('词条至少需要 2 个字')
    return
  }
  if (!meaning) {
    ElMessage.warning('请填写释义')
    return
  }
  if (jargonSaving.value) return
  jargonSaving.value = true
  try {
    const result = await chatApi.createJargon(tenant.value, term, meaning)
    jargonDialogVisible.value = false
    ElMessage.success(`已保存黑话「${result.item.term}」`)
    await loadJargon()
  } catch (error: any) {
    console.error('保存黑话失败', error)
    ElMessage.error(error?.response?.data?.detail || '保存黑话失败，请重试')
  } finally {
    jargonSaving.value = false
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
  reloadEmoji()
}

const setEmojiFormat = async (format: string) => {
  emojiFormat.value = format
  reloadEmoji()
}

const readBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

const openEmojiUpload = () => {
  emojiUploadFiles.value = []
  emojiDescription.value = ''
  emojiUploadVisible.value = true
}

const submitEmojiUpload = async () => {
  const file = emojiUploadFiles.value[0]?.raw
  if (!file || emojiUploading.value) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('只能上传图片文件')
    return
  }
  emojiUploading.value = true
  try {
    const content = await readBase64(file)
    const result = await chatApi.uploadEmoji(tenant.value, content, emojiDescription.value.trim())
    emojiUploadVisible.value = false
    emojiUploadFiles.value = []
    emojiDescription.value = ''
    if (result.warning) ElMessage.warning(result.warning)
    else ElMessage.success('表情已入库')
    await loadEmoji()
  } catch (error) {
    console.error('上传表情失败', error)
    ElMessage.error('上传表情失败，请重试')
  } finally {
    emojiUploading.value = false
  }
}

const openEmojiDetail = (row: ChatEmoji) => {
  emojiDetailRow.value = row
  emojiDetailVisible.value = true
}

const selectAllEmoji = () => {
  emojiSelection.value = emojis.value.map((row) => row.hash)
}

const editEmojiFromDetail = () => {
  if (emojiDetailRow.value) void editEmoji(emojiDetailRow.value)
}

const toggleEmojiFromDetail = () => {
  if (emojiDetailRow.value) void toggleEmoji(emojiDetailRow.value)
}

const registerEmojiFromDetail = () => {
  if (emojiDetailRow.value) void registerEmoji(emojiDetailRow.value)
}

const deleteEmojiFromDetail = () => {
  if (emojiDetailRow.value) void deleteEmoji(emojiDetailRow.value)
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

        <div :key="activeTab" class="tab-stack">
          <div v-if="activeTab === 'expression'" class="panel">
            <div class="panel-header">
              <div class="panel-heading">
                <h3 class="panel-title">表达方式</h3>
                <span class="panel-count">{{ expressionTotal }} 条</span>
              </div>
              <div class="filters">
                <el-select v-model="expressionStatus" class="status-select" @change="reloadExpressions">
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
                  @input="onExpressionSearch"
                  @clear="reloadExpressions"
                />
                <el-button :icon="Plus" :disabled="!tenant" @click="openExpressionDialog">添加</el-button>
                <el-button :disabled="!tenant" @click="openLearnDialog">从文本学习</el-button>
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
              :data="expressions"
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
            <ListPager
              v-model:page="expressionPage"
              :total="expressionTotal"
              :page-size="EXPRESSION_PAGE_SIZE"
            />

            <el-dialog v-model="expressionDialogVisible" title="添加表达方式" width="480" align-center>
              <el-form label-width="56px" @submit.prevent>
                <el-form-item label="情境">
                  <el-input
                    v-model="expressionForm.situation"
                    maxlength="60"
                    show-word-limit
                    placeholder="例如：对某件事表示惊叹时"
                  />
                </el-form-item>
                <el-form-item label="说法">
                  <el-input
                    v-model="expressionForm.style"
                    maxlength="60"
                    show-word-limit
                    placeholder="例如：我嘞个豆"
                  />
                </el-form-item>
              </el-form>
              <template #footer>
                <el-button @click="expressionDialogVisible = false">取消</el-button>
                <el-button type="primary" :loading="expressionSaving" @click="saveExpression">保存</el-button>
              </template>
            </el-dialog>

            <el-dialog v-model="learnDialogVisible" title="从文本学习表达" width="820" align-center>
              <div class="learn-dialog">
                <el-input
                  v-model="learnText"
                  type="textarea"
                  :rows="8"
                  maxlength="200000"
                  show-word-limit
                  placeholder="粘贴聊天记录或文本，模型会按情境和说法提取候选，确认后才写入"
                />
                <div class="learn-toolbar">
                  <span v-if="learnBusy">正在提取 {{ learnDone }}/{{ learnTotal }} 段…</span>
                  <span v-else-if="learnCandidates.length">已提取 {{ learnCandidates.length }} 条候选</span>
                  <span v-else class="learn-hint">提取结果不会自动生效，请先检查并修改候选。</span>
                  <div class="learn-actions">
                    <el-button size="small" :disabled="learnBusy" @click="resetLearned">清空</el-button>
                    <el-button
                      size="small"
                      type="primary"
                      :loading="learnBusy"
                      :disabled="!learnText.trim()"
                      @click="learnFromText"
                    >
                      提取候选
                    </el-button>
                  </div>
                </div>
                <el-alert v-if="learnFailed.length" type="warning" :closable="false" show-icon>
                  <template #title>有 {{ learnFailed.length }} 段提取失败</template>
                  <el-button link type="primary" :disabled="learnBusy" @click="retryFailedChunks">
                    重试失败段
                  </el-button>
                </el-alert>
                <el-table v-if="learnCandidates.length" :data="learnCandidates" max-height="360">
                  <el-table-column width="44">
                    <template #default="{ row }">
                      <el-checkbox v-model="row.selected" />
                    </template>
                  </el-table-column>
                  <el-table-column label="情境" min-width="200">
                    <template #default="{ row }">
                      <el-input v-model="row.situation" maxlength="60" />
                    </template>
                  </el-table-column>
                  <el-table-column label="说法" min-width="200">
                    <template #default="{ row }">
                      <el-input v-model="row.style" maxlength="60" />
                    </template>
                  </el-table-column>
                  <el-table-column label="原文依据" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.evidence || '—' }}</template>
                  </el-table-column>
                </el-table>
              </div>
              <template #footer>
                <div class="learn-footer">
                  <span class="learn-selected">已选 {{ selectedCandidateCount }} 条</span>
                  <el-button @click="learnDialogVisible = false">取消</el-button>
                  <el-button
                    type="primary"
                    :loading="learnImporting"
                    :disabled="!selectedCandidateCount"
                    @click="importLearnedExpressions"
                  >
                    导入选中
                  </el-button>
                </div>
              </template>
            </el-dialog>
          </div>

          <div v-if="activeTab === 'jargon'" class="panel">
            <div class="panel-header">
              <div class="panel-heading">
                <h3 class="panel-title">黑话</h3>
                <span class="panel-count">{{ jargonTotal }} 条</span>
              </div>
              <div class="filters">
                <el-select v-model="jargonStatus" class="status-select" @change="reloadJargon">
                  <el-option label="全部" value="all" />
                  <el-option label="生效中" value="active" />
                  <el-option label="待推断" value="pending" />
                  <el-option label="已拒绝" value="rejected" />
                </el-select>
                <el-input
                  v-model="jargonSearch"
                  class="search-input"
                  clearable
                  placeholder="搜索词条或释义"
                  :prefix-icon="Search"
                  @input="onJargonSearch"
                  @clear="reloadJargon"
                />
                <el-button :icon="Plus" :disabled="!tenant" @click="openJargonDialog">添加</el-button>
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
              :data="jargon"
              row-key="id"
              empty-text="暂无黑话条目"
              @selection-change="onJargonSelection"
            >
              <el-table-column type="selection" width="44" />
              <el-table-column prop="term" label="词条" min-width="130" show-overflow-tooltip />
              <el-table-column label="释义" min-width="240" show-overflow-tooltip>
                <template #default="{ row }">
                  <span :class="{ 'meaning-hint': !row.meaning }">{{ jargonMeaning(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="jargonTag(row.status).type" size="small">
                    {{ jargonTag(row.status).text }}
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
            <ListPager
              v-model:page="jargonPage"
              :total="jargonTotal"
              :page-size="JARGON_PAGE_SIZE"
            />
            <el-dialog v-model="jargonDialogVisible" title="添加黑话" width="480" align-center>
              <el-form label-position="top">
                <el-form-item label="黑话">
                  <el-input
                    v-model="jargonForm.term"
                    maxlength="30"
                    show-word-limit
                    placeholder="例如：绝绝子"
                  />
                </el-form-item>
                <el-form-item label="释义">
                  <el-input
                    v-model="jargonForm.meaning"
                    type="textarea"
                    :rows="3"
                    maxlength="1000"
                    show-word-limit
                    placeholder="例如：形容特别好"
                  />
                </el-form-item>
              </el-form>
              <template #footer>
                <el-button @click="jargonDialogVisible = false">取消</el-button>
                <el-button type="primary" :loading="jargonSaving" @click="saveJargon">保存</el-button>
              </template>
            </el-dialog>
          </div>

          <div v-if="activeTab === 'emoji'" class="panel">
            <div class="emoji-filters">
              <div class="filter-group" role="group" aria-label="表情状态">
                <button
                  v-for="filter in emojiFilters"
                  :key="filter.key"
                  type="button"
                  class="filter-status"
                  :class="{ active: emojiStatus === filter.key }"
                  @click="setEmojiStatus(filter.key)"
                >
                  <span class="status-dot" :class="`dot-${filter.key || 'all'}`" />
                  {{ filter.label }}
                  <span class="filter-count">{{ filter.count }}</span>
                </button>
              </div>
              <div class="filter-group" role="group" aria-label="表情格式">
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
              <el-select v-model="emojiSort" class="filter-sort" @change="reloadEmoji">
                <el-option label="最近加入" value="newest" />
                <el-option label="最早加入" value="oldest" />
                <el-option label="发送最多" value="count" />
                <el-option label="最近使用" value="last_used" />
                <el-option label="注册时间" value="register_time" />
              </el-select>
              <span class="filter-hint">
                聊天中收集的表情会自动识别并在名额内注册；已注册表情按描述匹配回复，同一表情冷却 5 分钟。
              </span>
            </div>

            <div class="emoji-main">
              <div class="emoji-toolbar">
                <el-input
                  v-model="emojiSearch"
                  class="emoji-search"
                  clearable
                  placeholder="搜索描述或标签"
                  :prefix-icon="Search"
                  @keyup.enter="reloadEmoji"
                  @clear="reloadEmoji"
                />
                <div class="toolbar-right">
                  <div class="card-size-group" role="group" aria-label="卡片大小">
                    <button
                      v-for="size in emojiCardSizes"
                      :key="size.key"
                      type="button"
                      class="card-size-item"
                      :class="{ active: emojiCardSize === size.key }"
                      :title="size.label"
                      @click="emojiCardSize = size.key"
                    >
                      <span :style="{ width: size.dot, height: size.dot }" />
                    </button>
                  </div>
                  <el-button
                    type="primary"
                    plain
                    :icon="Plus"
                    :disabled="!tenant"
                    @click="openEmojiUpload"
                  >
                    上传
                  </el-button>
                  <el-button :icon="Refresh" :loading="emojiLoading" @click="loadEmoji">刷新</el-button>
                </div>
              </div>
              <div v-if="emojiSelection.length" class="batch-bar">
                <span class="batch-info">已选择 {{ emojiSelection.length }} 个表情</span>
                <div class="batch-actions">
                  <el-button size="small" plain @click="selectAllEmoji">全选本页</el-button>
                  <el-button size="small" plain @click="emojiSelection = []">取消选择</el-button>
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :icon="Delete"
                    :disabled="acting"
                    @click="deleteSelectedEmoji"
                  >
                    批量删除
                  </el-button>
                </div>
              </div>
              <div class="emoji-grid" :class="`size-${emojiCardSize}`">
                <article v-for="row in emojis" :key="row.hash" class="emoji-card">
                  <div class="emoji-card-frame" @click="openEmojiDetail(row)">
                    <img
                      class="emoji-card-image"
                      :src="chatApi.emojiThumbnailUrl(tenant, row.hash)"
                      :alt="row.description"
                      loading="lazy"
                    >
                    <span class="emoji-card-format">{{ (row.format || 'img').toUpperCase() }}</span>
                    <span
                      class="emoji-card-dot"
                      :class="`dot-${row.status}`"
                      :title="emojiStatusText(row)"
                    />
                    <el-checkbox
                      class="emoji-card-check"
                      :model-value="emojiSelection.includes(row.hash)"
                      @click.stop
                      @change="toggleEmojiSelection(row.hash)"
                    />
                    <div class="emoji-card-actions" @click.stop>
                      <el-button link title="编辑描述" :icon="EditPen" :disabled="acting" @click="editEmoji(row)" />
                      <el-button
                        link
                        :title="row.registered ? '取消注册' : '注册'"
                        :icon="row.registered ? Close : Select"
                        :disabled="acting"
                        @click="registerEmoji(row)"
                      />
                      <el-button
                        link
                        :title="row.enabled ? '禁用' : '启用'"
                        :icon="row.enabled ? Hide : View"
                        :disabled="acting"
                        @click="toggleEmoji(row)"
                      />
                      <el-button
                        link
                        type="danger"
                        title="删除"
                        :icon="Delete"
                        :disabled="acting"
                        @click="deleteEmoji(row)"
                      />
                      <span class="emoji-card-usage" :title="`使用 ${row.count} 次`">×{{ row.count }}</span>
                    </div>
                  </div>
                  <p class="emoji-card-desc" :title="row.description || '暂无描述'">
                    {{ row.description || '暂无描述' }}
                  </p>
                </article>
              </div>
              <ListPager v-model:page="emojiPage" :total="emojiTotal" :page-size="EMOJI_PAGE_SIZE" />
              <el-empty v-if="!emojiLoading && !emojis.length" description="暂无表情" />
            </div>
          </div>
        </div>
      </section>
    </div>

    <el-dialog v-model="emojiDetailVisible" title="表情包详情" width="620" align-center>
      <div v-if="emojiDetailRow" class="emoji-detail">
        <div class="emoji-detail-media">
          <el-image
            :src="chatApi.emojiImageUrl(tenant, emojiDetailRow.hash)"
            :preview-src-list="[chatApi.emojiImageUrl(tenant, emojiDetailRow.hash)]"
            preview-teleported
            fit="contain"
            class="emoji-detail-image"
          />
        </div>
        <div class="emoji-detail-info">
          <div class="emoji-detail-row">
            <span class="emoji-detail-label">状态</span>
            <el-tag size="small" :type="emojiTagType(emojiDetailRow)">
              {{ emojiStatusText(emojiDetailRow) }}
            </el-tag>
            <el-tag size="small" effect="plain">
              {{ (emojiDetailRow.format || 'img').toUpperCase() }}
            </el-tag>
          </div>
          <div class="emoji-detail-row">
            <span class="emoji-detail-label">发送</span>
            <span>{{ emojiDetailRow.count }} 次</span>
          </div>
          <div class="emoji-detail-row">
            <span class="emoji-detail-label">加入</span>
            <span>{{ formatTime(emojiDetailRow.created_at) }}</span>
          </div>
          <div class="emoji-detail-row">
            <span class="emoji-detail-label">注册</span>
            <span>{{ formatTime(emojiDetailRow.register_time) }}</span>
          </div>
          <div class="emoji-detail-row">
            <span class="emoji-detail-label">最后使用</span>
            <span>{{ formatTime(emojiDetailRow.last_used) }}</span>
          </div>
          <div class="emoji-detail-hash">{{ emojiDetailRow.hash }}</div>
          <div class="emoji-detail-section">
            <h4 class="emoji-detail-label">描述</h4>
            <div class="emoji-detail-desc">{{ emojiDetailRow.description || '暂无描述' }}</div>
          </div>
          <div class="emoji-detail-section">
            <h4 class="emoji-detail-label">标签</h4>
            <div class="emoji-detail-tags">
              <el-tag v-for="tag in emojiDetailRow.tags" :key="tag" size="small" type="info">
                {{ tag }}
              </el-tag>
              <span v-if="!emojiDetailRow.tags.length" class="emoji-detail-empty">暂无标签</span>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="acting" @click="editEmojiFromDetail">编辑描述</el-button>
        <el-button :disabled="acting" @click="toggleEmojiFromDetail">
          {{ emojiDetailRow?.enabled ? '禁用' : '启用' }}
        </el-button>
        <el-button type="primary" :disabled="acting" @click="registerEmojiFromDetail">
          {{ emojiDetailRow?.registered ? '取消注册' : '注册' }}
        </el-button>
        <el-button type="danger" :disabled="acting" @click="deleteEmojiFromDetail">删除</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="emojiUploadVisible" title="上传表情" width="460" align-center>
      <div class="emoji-upload">
        <el-upload
          v-model:file-list="emojiUploadFiles"
          drag
          :auto-upload="false"
          :limit="1"
          accept="image/*"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽图片到此处，或<em>点击选择</em></div>
          <template #tip>
            <div class="upload-tip">支持 PNG/JPG/GIF/WEBP/BMP，最大 5 MB</div>
          </template>
        </el-upload>
        <el-input
          v-model="emojiDescription"
          clearable
          placeholder="描述（留空由视觉模型生成）"
        />
      </div>
      <template #footer>
        <el-button @click="emojiUploadVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="emojiUploading"
          :disabled="!emojiUploadFiles.length"
          @click="submitEmojiUpload"
        >
          上传
        </el-button>
      </template>
    </el-dialog>

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

.meaning-hint {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.learn-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.learn-toolbar,
.learn-actions,
.learn-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}

.learn-toolbar {
  justify-content: space-between;
}

.learn-hint,
.learn-selected {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.learn-footer .learn-selected {
  margin-right: auto;
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

.emoji-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  padding-bottom: 12px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--color-border-light);
}

.filter-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.filter-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }

  &.active {
    border-color: var(--color-primary);
    background: var(--color-primary-soft);
    color: var(--color-primary);
    font-weight: 600;
  }
}

.filter-count {
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
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

.filter-sort {
  width: 124px;
}

.filter-hint {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.emoji-main {
  min-width: 0;
  container-type: inline-size;
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

.emoji-search {
  flex: 1;
  min-width: 200px;
  max-width: 420px;
}

.status-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-tertiary);

  &.dot-registered {
    background: #3fbf6a;
  }

  &.dot-pending {
    background: #f2a04a;
  }

  &.dot-unidentified {
    background: #e6c143;
  }

  &.dot-disabled,
  &.dot-all {
    background: var(--color-text-tertiary);
  }
}

.card-size-group {
  display: flex;
  gap: 4px;
}

.card-size-item {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  cursor: pointer;

  span {
    border-radius: 1px;
    background: var(--color-text-tertiary);
  }

  &:hover {
    border-color: var(--color-text-tertiary);
  }

  &.active {
    border-color: var(--color-primary);
    background: var(--color-primary-soft);
  }

  &.active span {
    background: var(--color-primary);
  }
}

.emoji-grid {
  display: grid;
  gap: 16px 12px;
  margin-top: 14px;
  min-height: 80px;

  &.size-small,
  &.size-medium,
  &.size-large {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@container (min-width: 700px) {
  .emoji-grid {
    &.size-small {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }

    &.size-medium {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    &.size-large {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }
}

@container (min-width: 1100px) {
  .emoji-grid {
    &.size-small {
      grid-template-columns: repeat(8, minmax(0, 1fr));
    }

    &.size-medium {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }

    &.size-large {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
  }
}

.emoji-card {
  min-width: 0;
}

.emoji-card-frame {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-secondary);
  aspect-ratio: 1;
  cursor: pointer;

  &:hover {
    border-color: var(--color-primary);
  }
}

.emoji-card-image {
  display: block;
  width: 100%;
  height: 100%;
  padding: 4px;
  object-fit: contain;
  box-sizing: border-box;
}

.emoji-card-format {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(17, 24, 26, 0.55);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  letter-spacing: 0.04em;
}

.emoji-card-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-tertiary);

  &.dot-registered {
    background: #3fbf6a;
  }

  &.dot-pending {
    background: #f2a04a;
  }

  &.dot-unidentified {
    background: #e6c143;
  }

  &.dot-disabled {
    background: var(--color-text-tertiary);
  }
}

.emoji-card-check {
  position: absolute;
  top: 4px;
  right: 20px;
  height: 16px;
  margin-right: 0;
  padding: 0;
  opacity: 0;
  transition: opacity 0.15s ease;

  &.is-checked {
    opacity: 1;
  }

  :deep(.el-checkbox__inner) {
    background: var(--color-bg-card);
  }
}

.emoji-card-frame:hover .emoji-card-check {
  opacity: 1;
}

.emoji-card-actions {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 5px;
  background: color-mix(in srgb, var(--color-bg-card) 88%, transparent);

  .el-button {
    padding: 2px;
    color: var(--color-text-secondary);

    &:hover {
      color: var(--color-primary);
    }
  }

  .el-button.el-button--danger:hover {
    color: var(--color-danger, #f56c6c);
  }
}

.emoji-card-usage {
  margin-left: auto;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--color-text);
  color: var(--color-bg-card);
  font-size: 11px;
  line-height: 16px;
  font-variant-numeric: tabular-nums;
}

.emoji-card-desc {
  display: -webkit-box;
  min-height: 36px;
  margin: 6px 2px 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 18px;
  word-break: break-all;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.emoji-detail {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 20px;
}

.emoji-detail-media {
  display: flex;
  align-self: start;
  align-items: flex-start;
  justify-content: center;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
}

.emoji-detail-image {
  width: 200px;
  height: 200px;
  cursor: zoom-in;
}

.emoji-detail-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.emoji-detail-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.emoji-detail-label {
  flex: none;
  width: 62px;
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 600;
}

.emoji-detail-hash {
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-family: 'Courier New', monospace;
  font-size: 11px;
  word-break: break-all;
}

.emoji-detail-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.emoji-detail-desc {
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.emoji-detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.emoji-detail-empty {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.emoji-upload {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upload-icon {
  margin-bottom: 6px;
  color: var(--color-text-tertiary);
  font-size: 40px;
}

.upload-tip {
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
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

  .filter-hint {
    margin-left: 0;
  }

  .search-input,
  .emoji-search {
    width: 100%;
    max-width: none;
  }

  .emoji-detail {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
