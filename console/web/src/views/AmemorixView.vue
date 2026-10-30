<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Refresh, Search, Upload } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import ChatScopeList from '@/components/ChatScopeList.vue'
import { amemorixApi, chatApi } from '@/api'
import type {
  AmemorixBundle,
  AmemorixCorrectionItem,
  AmemorixCorrectionPreview,
  AmemorixCorrectionTarget,
  AmemorixGraph,
  AmemorixGraphNodeDetail,
  AmemorixImage,
  AmemorixImageDetail,
  AmemorixImageStatus,
  AmemorixImportSettings,
  AmemorixImportTask,
  AmemorixProfile,
  AmemorixProfileDetail,
  AmemorixRecord,
  AmemorixRecordDetail,
  AmemorixRecycleItem,
  AmemorixRuntime,
  AmemorixSource,
  AmemorixStatus,
  AmemorixTimelineItem,
  ChatTenant,
} from '@/types/api'

type TabKey = 'query' | 'images' | 'timeline' | 'transfer' | 'inspect' | 'erase'
type QueryMode = 'records' | 'profiles'
type TransferMode = 'bundles' | 'imports'
type InspectMode = 'corrections' | 'runtime' | 'episodes' | 'graph'

const TAB_KEY = 'fxbot-amemorix-tab'
const TENANT_KEY = 'fxbot-amemorix-tenant'

const tabs = [
  { key: 'query', label: '记忆查询' },
  { key: 'images', label: '图片记忆' },
  { key: 'timeline', label: '记忆流' },
  { key: 'transfer', label: '导入导出' },
  { key: 'inspect', label: '记忆检修' },
  { key: 'erase', label: '记忆抹除' },
] as const

const queryTabs = [
  { key: 'records', label: '文字记录' },
  { key: 'profiles', label: '人物画像' },
] as const

const transferTabs = [
  { key: 'bundles', label: '记忆包' },
  { key: 'imports', label: '导入任务' },
] as const

const inspectTabs = [
  { key: 'corrections', label: '纠正记录' },
  { key: 'runtime', label: '运行与配置' },
  { key: 'episodes', label: '情景片段' },
  { key: 'graph', label: '关系图谱' },
] as const

const LEGACY_TABS: Record<
  string,
  { tab: TabKey; query?: QueryMode; transfer?: TransferMode; inspect?: InspectMode }
> = {
  records: { tab: 'query', query: 'records' },
  profiles: { tab: 'query', query: 'profiles' },
  episodes: { tab: 'inspect', inspect: 'episodes' },
  graph: { tab: 'inspect', inspect: 'graph' },
  recycle: { tab: 'erase' },
  corrections: { tab: 'inspect', inspect: 'corrections' },
  bundles: { tab: 'transfer', transfer: 'bundles' },
  imports: { tab: 'transfer', transfer: 'imports' },
  images: { tab: 'images' },
  timeline: { tab: 'timeline' },
  runtime: { tab: 'inspect', inspect: 'runtime' },
}

const savedKey = localStorage.getItem(TAB_KEY) || ''
const legacy = LEGACY_TABS[savedKey]
const fallbackTab = tabs.some((tab) => tab.key === savedKey) ? (savedKey as TabKey) : 'query'

const activeTab = ref<TabKey>(legacy ? legacy.tab : fallbackTab)
const queryMode = ref<QueryMode>(legacy?.query || 'records')
const transferMode = ref<TransferMode>(legacy?.transfer || 'bundles')
const inspectMode = ref<InspectMode>(legacy?.inspect || 'corrections')

const typeLabels: Record<string, string> = {
  paragraph: '段落',
  entity: '实体',
  relation: '关系',
  episode: '片段',
  fact: '事实',
}

const statusLabels: Record<string, string> = {
  active: '有效',
  deleted: '已删除',
  inactive: '已失效',
  retracted: '已撤回',
}

const categoryLabels: Record<string, string> = {
  paragraph: '段落',
  episode: '情景',
  fact: '事实',
  delete: '删除',
  feedback: '反馈',
  correction: '修正',
}

const eventLabels: Record<string, string> = {
  paragraph_created: '新增段落',
  paragraph_updated: '更新段落',
  paragraph_deleted: '删除段落',
  episode_created: '生成情景',
  fact_created: '写入事实',
  fact_retracted: '撤回事实',
  memory_deleted: '删除记忆',
  memory_restored: '恢复记忆',
  feedback_correction: '反馈纠错',
  memory_correction: '模糊修正',
}

const actionLabels: Record<string, string> = {
  mark_superseded: '旧记忆失效',
  ingest_text: '写入新记忆',
  refresh_person_profile: '刷新画像',
}

const imageSourceLabels: Record<string, string> = {
  chat: '聊天消息',
  manual: '手动写入',
  user_statement: '消息原话',
  model_description: '模型识别',
}

const imageConfirmLabels: Record<string, string> = {
  unconfirmed: '待核对',
  confirmed: '已确认',
}

const tenant = ref(localStorage.getItem(TENANT_KEY) || '')
const tenants = ref<ChatTenant[]>([])
const loaded = ref(false)
const loading = ref(false)
const status = ref<AmemorixStatus | null>(null)

const records = ref<AmemorixRecord[]>([])
const memoryQuery = ref('')
const recordTypes = ref<string[]>(['paragraph', 'entity', 'relation', 'episode', 'fact'])
const includeInactive = ref(false)
const recordLoading = ref(false)
const selectedRecord = ref<AmemorixRecord | null>(null)
const recordDetail = ref<AmemorixRecordDetail | null>(null)
const recordDrawer = ref(false)
const detailLoading = ref(false)
const selectedKeys = ref<string[]>([])

const correctionDialog = ref(false)
const correctionRequest = ref('')
const correctionPreviewing = ref(false)
const correctionExecuting = ref(false)
const correctionPlan = ref<AmemorixCorrectionPreview | null>(null)

const profiles = ref<AmemorixProfile[]>([])
const profileLoading = ref(false)
const selectedProfile = ref<AmemorixProfile | null>(null)
const profileDetail = ref<AmemorixProfileDetail | null>(null)
const profileDrawer = ref(false)

const episodes = ref<AmemorixRecord[]>([])
const episodeQuery = ref('')
const episodeLoading = ref(false)

const graph = ref<AmemorixGraph>({ nodes: [], edges: [], total_nodes: 0, total_edges: 0 })
const graphQuery = ref('')
const graphLoading = ref(false)
const selectedNode = ref('')
const nodeDetail = ref<AmemorixGraphNodeDetail | null>(null)
const nodeLoading = ref(false)

const runtime = ref<AmemorixRuntime | null>(null)
const sources = ref<AmemorixSource[]>([])
const runtimeLoading = ref(false)
const selfChecking = ref(false)
const configText = ref('')
const configSaving = ref(false)

const timelineItems = ref<AmemorixTimelineItem[]>([])
const timelineLoading = ref(false)
const recycleItems = ref<AmemorixRecycleItem[]>([])
const recycleLoading = ref(false)
const correctionItems = ref<AmemorixCorrectionItem[]>([])
const correctionKind = ref('')
const correctionLoading = ref(false)
const bundles = ref<AmemorixBundle[]>([])
const bundleLoading = ref(false)
const bundleDownload = ref<{ name: string; url: string } | null>(null)
const bundleGlobal = ref(false)
const bundleInput = ref<HTMLInputElement>()
const importTasks = ref<AmemorixImportTask[]>([])
const importSettings = ref<AmemorixImportSettings | null>(null)
const importText = ref('')
const importName = ref('')
const importLlm = ref(true)
const importLoading = ref(false)
const importInput = ref<HTMLInputElement>()
const IMAGE_PAGE_SIZE = 24
const images = ref<AmemorixImage[]>([])
const imageOffset = ref(0)
const imageStatus = ref<AmemorixImageStatus | null>(null)
const imageLoading = ref(false)
const imageDescription = ref('')
const imageInput = ref<HTMLInputElement>()
const imageDetailVisible = ref(false)
const imageDetailLoading = ref(false)
const imageDetail = ref<AmemorixImageDetail | null>(null)
const imageDetailRow = ref<AmemorixImage | null>(null)

const fileToBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

const formatTime = (value: string | null | undefined) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const formatEpoch = (value: number | null | undefined) => {
  if (!value) return '—'
  return new Date(value * 1000).toLocaleString()
}

const formatBytes = (value: number | null | undefined) => {
  const size = Number(value) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const imageDetailOccurrences = computed(() => imageDetail.value?.occurrences || [])

const imageDetailOccurrence = computed(() => {
  const rows = imageDetailOccurrences.value
  const target = imageDetailRow.value?.occurrence_id
  return rows.find((item) => item.occurrence_id === target) || rows[0] || null
})

const chatLabel = (value: string) => {
  const parts = String(value || '').split(':')
  const id = parts[parts.length - 1]
  if (parts.length >= 2 && /^\d+$/.test(id)) {
    if (parts[parts.length - 2] === 'private') return `私聊 ${id}`
    if (parts[parts.length - 2] === 'group') return `群聊 ${id}`
  }
  return value || '—'
}

const observationText = (text: string) => {
  const lines = String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !/^\[(图片|表情)\]$/.test(line))
  if (lines.length && /^.{1,60}\(\d+\)[:：]$/.test(lines[0])) lines.shift()
  return lines.join('\n')
}

const observationLabel = (sourceKind: string, confirmStatus: string) => {
  const source = imageSourceLabels[sourceKind] || sourceKind
  const confirm = imageConfirmLabels[confirmStatus]
  return confirm ? `${source} · ${confirm}` : source
}

const imageDetailObservations = computed(() => {
  const seen = new Set<string>()
  return (imageDetail.value?.observations || [])
    .map((item) => ({ ...item, text: observationText(item.text) }))
    .filter((item) => {
      if (!item.text) return false
      const key = `${item.source_kind}:${item.text}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
})

const imageDetailLinks = computed(() => (imageDetail.value?.links || []).filter((item) => item.memory))

const filteredNodes = computed(() => {
  const keyword = graphQuery.value.trim().toLowerCase()
  if (!keyword) return graph.value.nodes
  return graph.value.nodes.filter((node) => node.name.toLowerCase().includes(keyword))
})

const tenantReady = computed(() => Boolean(tenant.value))

const recordKey = (row: AmemorixRecord) => `${row.type}:${row.id}`

const isCorrectionTarget = (row: AmemorixRecord) => row.type === 'paragraph' || row.type === 'relation'

const selectedTargets = computed<AmemorixCorrectionTarget[]>(() =>
  records.value
    .filter((row) => selectedKeys.value.includes(recordKey(row)) && isCorrectionTarget(row))
    .map((row) => ({ type: row.type as 'paragraph' | 'relation', id: row.id })),
)

const selectableRecords = computed(() => records.value.filter(isCorrectionTarget))

const allSelected = computed(
  () =>
    selectableRecords.value.length > 0 &&
    selectableRecords.value.every((row) => selectedKeys.value.includes(recordKey(row))),
)

const statusTagType = (value: string): 'success' | 'info' | 'warning' | 'danger' => {
  if (value === 'active') return 'success'
  if (value === 'deleted' || value === 'retracted') return 'danger'
  return 'info'
}

const loadTenants = async () => {
  try {
    const result = await chatApi.tenants()
    tenants.value = result.tenants || []
    if (tenant.value && !tenants.value.some((item) => item.tenant === tenant.value)) tenant.value = ''
    if (!tenant.value && tenants.value.length) tenant.value = tenants.value[0].tenant
  } catch (error) {
    console.error('加载会话失败', error)
  }
}

const loadStatus = async () => {
  if (!tenantReady.value) return
  status.value = await amemorixApi.status(tenant.value)
}

const loadRecords = async () => {
  if (!tenantReady.value) return
  recordLoading.value = true
  try {
    const result = await amemorixApi.records(tenant.value, {
      query: memoryQuery.value.trim(),
      types: recordTypes.value.join(','),
      limit: 80,
      includeInactive: includeInactive.value,
    })
    records.value = result.items || []
    const available = new Set(selectableRecords.value.map(recordKey))
    selectedKeys.value = selectedKeys.value.filter((key) => available.has(key))
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载记忆记录失败')
  } finally {
    recordLoading.value = false
  }
}

const openRecord = async (row: AmemorixRecord) => {
  selectedRecord.value = row
  recordDrawer.value = true
  detailLoading.value = true
  recordDetail.value = null
  try {
    recordDetail.value = await amemorixApi.record(tenant.value, row.type, row.id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

const removeRecord = async () => {
  const row = selectedRecord.value
  if (!row) return
  if (row.type === 'episode') {
    ElMessage.warning('情景片段由段落自动生成，不能单独删除')
    return
  }
  const action = row.type === 'fact' ? '撤回' : '删除'
  try {
    await ElMessageBox.confirm(`确认${action}「${row.title}」？`, '确认操作', { type: 'warning' })
  } catch {
    return
  }
  try {
    await amemorixApi.deleteRecord(tenant.value, row.type, row.id)
    ElMessage.success(`${action}成功`)
    recordDrawer.value = false
    await Promise.all([loadRecords(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || `${action}失败`)
  }
}

const toggleRecord = (row: AmemorixRecord) => {
  const key = recordKey(row)
  selectedKeys.value = selectedKeys.value.includes(key)
    ? selectedKeys.value.filter((item) => item !== key)
    : [...selectedKeys.value, key]
}

const toggleSelectAll = () => {
  selectedKeys.value = allSelected.value ? [] : selectableRecords.value.map(recordKey)
}

const openCorrection = () => {
  if (!selectedTargets.value.length) {
    ElMessage.warning('请先选择要修正的段落或关系')
    return
  }
  correctionRequest.value = ''
  correctionPlan.value = null
  correctionDialog.value = true
}

const previewCorrection = async () => {
  if (!correctionRequest.value.trim()) {
    ElMessage.warning('请填写修改内容')
    return
  }
  correctionPreviewing.value = true
  try {
    correctionPlan.value = await amemorixApi.previewCorrection({
      tenant: tenant.value,
      request_text: correctionRequest.value.trim(),
      targets: selectedTargets.value,
    })
    ElMessage.success('已生成修改计划，请确认后执行')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '生成修正计划失败')
  } finally {
    correctionPreviewing.value = false
  }
}

const executeCorrection = async () => {
  const planId = correctionPlan.value?.plan_id
  if (!planId || correctionExecuting.value) return
  correctionExecuting.value = true
  try {
    await amemorixApi.executeCorrection({ tenant: tenant.value, plan_id: planId })
    ElMessage.success('修正已执行')
    correctionDialog.value = false
    correctionPlan.value = null
    selectedKeys.value = []
    await Promise.all([loadRecords(), loadStatus(), loadCorrections()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '执行修正失败')
  } finally {
    correctionExecuting.value = false
  }
}

watch(correctionRequest, () => {
  if (correctionPlan.value) correctionPlan.value = null
})

const loadProfiles = async () => {
  if (!tenantReady.value) return
  profileLoading.value = true
  try {
    const result = await amemorixApi.profiles(tenant.value, memoryQuery.value.trim())
    profiles.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载人物画像失败')
  } finally {
    profileLoading.value = false
  }
}

const openProfile = async (row: AmemorixProfile) => {
  selectedProfile.value = row
  profileDrawer.value = true
  profileDetail.value = null
  try {
    profileDetail.value = await amemorixApi.profile(tenant.value, row.person_id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载画像详情失败')
  }
}

const loadEpisodes = async () => {
  if (!tenantReady.value) return
  episodeLoading.value = true
  try {
    const result = await amemorixApi.episodes(tenant.value, episodeQuery.value.trim())
    episodes.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载情景片段失败')
  } finally {
    episodeLoading.value = false
  }
}

const loadGraph = async () => {
  if (!tenantReady.value) return
  graphLoading.value = true
  try {
    graph.value = await amemorixApi.graph(tenant.value)
    selectedNode.value = ''
    nodeDetail.value = null
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载关系图谱失败')
  } finally {
    graphLoading.value = false
  }
}

const openNode = async (node: string) => {
  selectedNode.value = node
  nodeLoading.value = true
  nodeDetail.value = null
  try {
    nodeDetail.value = await amemorixApi.graphNode(tenant.value, node)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载节点详情失败')
  } finally {
    nodeLoading.value = false
  }
}

const loadRuntime = async () => {
  runtimeLoading.value = true
  try {
    const [runtimeResult, sourceResult, configResult] = await Promise.all([
      amemorixApi.runtime(),
      tenantReady.value ? amemorixApi.sources(tenant.value) : Promise.resolve({ items: [] }),
      amemorixApi.getConfig(),
    ])
    runtime.value = runtimeResult
    sources.value = sourceResult.items || []
    configText.value = JSON.stringify(configResult.config || {}, null, 2)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载运行状态失败')
  } finally {
    runtimeLoading.value = false
  }
}

const runSelfCheck = async () => {
  selfChecking.value = true
  try {
    const result = await amemorixApi.selfCheck()
    ElMessage[result.success ? 'success' : 'warning'](result.success ? '自检通过' : '自检未通过，已降级')
    await Promise.all([loadStatus(), loadRuntime()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '自检失败')
  } finally {
    selfChecking.value = false
  }
}

const saveConfig = async () => {
  let parsed: Record<string, any>
  try {
    parsed = JSON.parse(configText.value)
  } catch {
    ElMessage.error('配置不是合法 JSON')
    return
  }
  try {
    await ElMessageBox.confirm('保存后需要重启 Bot 才会完全生效，确认保存？', '保存配置', { type: 'warning' })
  } catch {
    return
  }
  configSaving.value = true
  try {
    await amemorixApi.saveConfig(parsed)
    ElMessage.success('配置已保存，重启后生效')
    await loadStatus()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally {
    configSaving.value = false
  }
}

const loadTimeline = async () => {
  if (!tenantReady.value) return
  timelineLoading.value = true
  try {
    timelineItems.value = (await amemorixApi.timeline(tenant.value)).items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载时间线失败')
  } finally {
    timelineLoading.value = false
  }
}

const loadRecycle = async () => {
  if (!tenantReady.value) return
  recycleLoading.value = true
  try {
    recycleItems.value = (await amemorixApi.recycle(tenant.value)).items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载回收站失败')
  } finally {
    recycleLoading.value = false
  }
}

const restoreRecycle = async (row: AmemorixRecycleItem) => {
  try {
    await ElMessageBox.confirm(`确认恢复「${row.title}」？`, '恢复记忆', { type: 'warning' })
  } catch {
    return
  }
  try {
    await amemorixApi.restoreRecycle({ tenant: tenant.value, kind: row.kind, id: row.id })
    ElMessage.success('已恢复')
    await Promise.all([loadRecycle(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '恢复失败')
  }
}

const purgeRecycle = async () => {
  try {
    await ElMessageBox.confirm(
      '将永久清理超过 24 小时的已删除记忆，且无法恢复。确认继续？',
      '清理回收站',
      { type: 'warning', confirmButtonText: '永久清理' },
    )
  } catch {
    return
  }
  try {
    await amemorixApi.purgeRecycle(tenant.value)
    ElMessage.success('清理完成')
    await Promise.all([loadRecycle(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '清理失败')
  }
}

const loadCorrections = async () => {
  if (!tenantReady.value) return
  correctionLoading.value = true
  try {
    correctionItems.value =
      (await amemorixApi.corrections(tenant.value, correctionKind.value)).items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载纠正记录失败')
  } finally {
    correctionLoading.value = false
  }
}

const rollbackCorrection = async (row: AmemorixCorrectionItem) => {
  try {
    await ElMessageBox.confirm(`确认回滚「${row.title}」？`, '回滚修正', { type: 'warning' })
  } catch {
    return
  }
  try {
    await amemorixApi.rollbackCorrection({ tenant: tenant.value, kind: row.kind, id: row.id })
    ElMessage.success('已回滚')
    await Promise.all([loadCorrections(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '回滚失败')
  }
}

const loadBundles = async () => {
  bundleLoading.value = true
  try {
    bundles.value = (await amemorixApi.bundles()).items || []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载记忆包失败')
  } finally {
    bundleLoading.value = false
  }
}

const exportBundle = async (global: boolean) => {
  if (!global && !tenantReady.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  try {
    const result = await amemorixApi.exportBundle(global ? '' : tenant.value)
    const name = String(result.file_name || '')
    if (!name) {
      ElMessage.warning('没有可导出的记忆')
      return
    }
    bundleDownload.value = { name, url: amemorixApi.bundleFileUrl(name) }
    ElMessage.success(`已导出 ${name}`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '导出失败')
  }
}

const chooseBundleFile = () => bundleInput.value?.click()

const onBundleFile = async (event: Event) => {
  const file = (event.target as HTMLInputElement).files?.[0]
  ;(event.target as HTMLInputElement).value = ''
  if (!file) return
  try {
    const content = await fileToBase64(file)
    await amemorixApi.importBundle({
      file_name: file.name,
      content_base64: content,
      tenant: bundleGlobal.value ? '' : tenant.value,
    })
    ElMessage.success('记忆包已安装')
    await Promise.all([loadBundles(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '安装记忆包失败')
  }
}

const uninstallBundle = async (row: AmemorixBundle) => {
  try {
    await ElMessageBox.confirm(`确认卸载「${row.name || row.package_id}」？`, '卸载记忆包', { type: 'warning' })
  } catch {
    return
  }
  try {
    await amemorixApi.uninstallBundle(row.installation_id)
    ElMessage.success('已卸载')
    await Promise.all([loadBundles(), loadStatus()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '卸载失败')
  }
}

const loadImports = async () => {
  importLoading.value = true
  try {
    const result = await amemorixApi.imports()
    importTasks.value = result.items || []
    importSettings.value = result.settings || null
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载导入任务失败')
  } finally {
    importLoading.value = false
  }
}

const submitImport = async () => {
  if (!tenantReady.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  if (!importText.value.trim()) {
    ElMessage.warning('导入内容不能为空')
    return
  }
  importLoading.value = true
  try {
    await amemorixApi.pasteImport({
      tenant: tenant.value,
      name: importName.value.trim() || 'paste.txt',
      content: importText.value,
      llm_enabled: importLlm.value,
    })
    importText.value = ''
    importName.value = ''
    ElMessage.success('已创建导入任务')
    await loadImports()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '创建导入任务失败')
  } finally {
    importLoading.value = false
  }
}

const chooseImportFile = () => importInput.value?.click()

const onImportFile = async (event: Event) => {
  const file = (event.target as HTMLInputElement).files?.[0]
  ;(event.target as HTMLInputElement).value = ''
  if (!file) return
  if (!tenantReady.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  try {
    const content = await fileToBase64(file)
    await amemorixApi.uploadImport({
      tenant: tenant.value,
      file_name: file.name,
      content_base64: content,
      llm_enabled: importLlm.value,
    })
    ElMessage.success('已创建导入任务')
    await loadImports()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '上传导入失败')
  }
}

const cancelImportTask = async (row: AmemorixImportTask) => {
  try {
    await amemorixApi.cancelImport(row.task_id)
    ElMessage.success('已请求取消')
    await loadImports()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '取消失败')
  }
}

const retryImportTask = async (row: AmemorixImportTask) => {
  try {
    await amemorixApi.retryImport(row.task_id)
    ElMessage.success('已提交重试')
    await loadImports()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试失败')
  }
}

const loadImages = async () => {
  if (!tenantReady.value) return
  imageLoading.value = true
  try {
    const [result, statusResult] = await Promise.all([
      amemorixApi.images(tenant.value, IMAGE_PAGE_SIZE, imageOffset.value),
      amemorixApi.imageStatus(),
    ])
    images.value = result.items || []
    imageStatus.value = statusResult
    if (result.error) ElMessage.warning(result.error)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载图片记忆失败')
  } finally {
    imageLoading.value = false
  }
}

const stepImagePage = async (delta: number) => {
  imageOffset.value = Math.max(0, imageOffset.value + delta * IMAGE_PAGE_SIZE)
  await loadImages()
}

const chooseImageFile = () => imageInput.value?.click()

const onImageFile = async (event: Event) => {
  const file = (event.target as HTMLInputElement).files?.[0]
  ;(event.target as HTMLInputElement).value = ''
  if (!file) return
  if (!tenantReady.value) {
    ElMessage.warning('请先选择会话')
    return
  }
  try {
    await amemorixApi.uploadImage({
      tenant: tenant.value,
      content_base64: await fileToBase64(file),
      description: imageDescription.value.trim(),
    })
    imageDescription.value = ''
    ElMessage.success('图片记忆已写入')
    imageOffset.value = 0
    await loadImages()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '写入图片记忆失败')
  }
}

const openImageDetail = async (row: AmemorixImage) => {
  imageDetailRow.value = row
  imageDetail.value = null
  imageDetailVisible.value = true
  imageDetailLoading.value = true
  try {
    imageDetail.value = await amemorixApi.imageDetail(tenant.value, row.asset_id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载图片详情失败')
    imageDetailVisible.value = false
  } finally {
    imageDetailLoading.value = false
  }
}

const removeImage = async (row: AmemorixImage) => {
  if (!row.occurrence_id) {
    ElMessage.warning('当前会话没有这张图片的出现记录')
    return false
  }
  try {
    await ElMessageBox.confirm('确认删除这张图片记忆？', '删除图片', { type: 'warning' })
  } catch {
    return false
  }
  try {
    await amemorixApi.deleteImage(tenant.value, row.occurrence_id)
    ElMessage.success('已删除')
    await loadImages()
    // 删掉当前页最后一张时退回上一页
    if (!images.value.length && imageOffset.value > 0) {
      imageOffset.value = Math.max(0, imageOffset.value - IMAGE_PAGE_SIZE)
      await loadImages()
    }
    return true
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '删除失败')
    return false
  }
}

const removeImageFromDetail = async () => {
  if (!imageDetailRow.value) return
  if (await removeImage(imageDetailRow.value)) imageDetailVisible.value = false
}

const retryImages = async () => {
  try {
    const result = await amemorixApi.retryImages()
    ElMessage.success(`已重试 ${result.count ?? 0} 个任务`)
    await loadImages()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试失败')
  }
}

const loadQuery = async () => {
  if (queryMode.value === 'records') await loadRecords()
  else await loadProfiles()
}

const loadTransfer = async () => {
  if (transferMode.value === 'bundles') await loadBundles()
  else await loadImports()
}

const loadInspect = async () => {
  if (inspectMode.value === 'corrections') await loadCorrections()
  else if (inspectMode.value === 'runtime') await loadRuntime()
  else if (inspectMode.value === 'episodes') await loadEpisodes()
  else await loadGraph()
}

const loadCurrent = async () => {
  if (activeTab.value === 'query') await loadQuery()
  else if (activeTab.value === 'images') await loadImages()
  else if (activeTab.value === 'timeline') await loadTimeline()
  else if (activeTab.value === 'transfer') await loadTransfer()
  else if (activeTab.value === 'inspect') await loadInspect()
  else await loadRecycle()
}

const refreshCurrent = async () => {
  loading.value = true
  try {
    await loadStatus()
    await loadCurrent()
  } finally {
    loaded.value = true
    loading.value = false
  }
}

const onTenantChange = async () => {
  localStorage.setItem(TENANT_KEY, tenant.value)
  imageOffset.value = 0
  recordDrawer.value = false
  profileDrawer.value = false
  selectedKeys.value = []
  await refreshCurrent()
}

watch(activeTab, async (value) => {
  localStorage.setItem(TAB_KEY, value)
  if (!tenantReady.value && value !== 'transfer') return
  await loadCurrent()
})

watch(queryMode, async () => {
  if (activeTab.value === 'query' && loaded.value) await loadQuery()
})

watch(transferMode, async () => {
  if (activeTab.value === 'transfer' && loaded.value) await loadTransfer()
})

watch(inspectMode, async () => {
  if (activeTab.value === 'inspect' && loaded.value) await loadInspect()
})

onMounted(async () => {
  await loadTenants()
  await refreshCurrent()
})
</script>

<template>
  <div class="amemorix-view">
    <PageHeader title="长期记忆" subtitle="记忆查询、图片、记忆流、导入导出、检修与抹除，全部按会话隔离">
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="refreshCurrent">刷新</el-button>
      </template>
    </PageHeader>

    <div class="memory-layout">
      <ChatScopeList v-model="tenant" :tenants="tenants" title="记忆范围" @change="onTenantChange" />

      <section class="memory-main">
        <div class="main-head">
          <PillTabs v-if="loaded" v-model="activeTab" :items="tabs" />
        </div>

        <Transition name="tab-swap" mode="out-in">
          <div :key="activeTab" class="tab-stack">
            <div v-if="activeTab === 'query'" class="panel">
              <div class="panel-header">
                <div class="panel-heading">
                  <h3 class="panel-title">记忆查询</h3>
                  <span class="panel-count">
                    {{ queryMode === 'records' ? records.length : profiles.length }} 条
                  </span>
                </div>
                <div class="filters">
                  <el-input
                    v-model="memoryQuery"
                    class="keyword-input"
                    clearable
                    :placeholder="queryMode === 'records' ? '搜索内容、实体或事实' : '按姓名或画像内容搜索'"
                    :prefix-icon="Search"
                    @keyup.enter="loadQuery"
                    @clear="loadQuery"
                  />
                  <el-button @click="loadQuery">查询</el-button>
                </div>
              </div>

              <div class="query-subhead">
                <PillTabs v-model="queryMode" :items="queryTabs" size="small" />
                <div v-if="status" class="memory-summary">
                  <span>段落 {{ status.counts.paragraphs }}</span>
                  <span>实体 {{ status.counts.entities }}</span>
                  <span>关系 {{ status.counts.relations }}</span>
                  <span>情景 {{ status.counts.episodes }}</span>
                  <span>画像 {{ status.counts.profiles }}</span>
                </div>
              </div>

              <template v-if="queryMode === 'records'">
                <div class="query-toolbar">
                  <div class="query-filters">
                    <el-select
                      v-model="recordTypes"
                      multiple
                      collapse-tags
                      class="type-select"
                      placeholder="类型"
                      @change="loadRecords"
                    >
                      <el-option v-for="(label, key) in typeLabels" :key="key" :label="label" :value="key" />
                    </el-select>
                    <el-switch v-model="includeInactive" active-text="含失效" @change="loadRecords" />
                  </div>
                  <div class="batch-bar">
                    <el-button
                      size="small"
                      text
                      :disabled="!selectableRecords.length"
                      @click="toggleSelectAll"
                    >
                      {{ allSelected ? '取消全选' : '全选' }}
                    </el-button>
                    <span class="batch-info">已选 {{ selectedTargets.length }} 条</span>
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      :disabled="!selectedTargets.length"
                      @click="openCorrection"
                    >
                      修正所选
                    </el-button>
                    <el-button size="small" :disabled="!selectedKeys.length" @click="selectedKeys = []">
                      清空
                    </el-button>
                  </div>
                </div>

                <div class="record-list">
                  <div
                    v-for="row in records"
                    :key="recordKey(row)"
                    class="record-row"
                    :class="{ selected: selectedKeys.includes(recordKey(row)) }"
                    @click="openRecord(row)"
                  >
                    <el-checkbox
                      class="record-check"
                      :model-value="selectedKeys.includes(recordKey(row))"
                      :disabled="!isCorrectionTarget(row)"
                      :title="isCorrectionTarget(row) ? '选中后可批量修正' : '只有段落和关系可以批量修正'"
                      @click.stop
                      @change="toggleRecord(row)"
                    />
                    <el-tag class="record-type" size="small" effect="plain">
                      {{ typeLabels[row.type] || row.type }}
                    </el-tag>
                    <div class="record-content">
                      <div class="record-title">{{ row.title }}</div>
                      <div class="record-summary">{{ row.summary }}</div>
                    </div>
                    <div class="record-source">{{ row.source_label || '—' }}</div>
                    <el-tag size="small" :type="statusTagType(row.status)">
                      {{ statusLabels[row.status] || row.status }}
                    </el-tag>
                    <div class="record-time">{{ formatTime(row.updated_at || row.created_at) }}</div>
                  </div>
                  <el-empty v-if="!recordLoading && !records.length" description="没有记忆记录" />
                </div>
                <div class="query-hint">批量修正只支持段落与关系；其他类型请在详情中单独处理。</div>
              </template>

              <template v-else>
                <el-table
                  :data="profiles"
                  empty-text="还没有生成画像"
                  @row-click="openProfile"
                >
                  <el-table-column label="人物" width="160">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.name }}</div>
                      <div class="record-summary">{{ row.user_id }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="画像" min-width="320">
                    <template #default="{ row }">
                      <div class="record-summary clamp-2">{{ row.profile_text || '尚未生成画像正文' }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="事实" width="76" prop="fact_count" />
                  <el-table-column label="版本" width="76" prop="profile_version" />
                  <el-table-column label="更新时间" width="170">
                    <template #default="{ row }">{{ row.updated_at || '—' }}</template>
                  </el-table-column>
                </el-table>
              </template>
            </div>

            <div v-if="activeTab === 'images'" class="panel">
              <div class="panel-header">
                <div class="panel-heading">
                  <h3 class="panel-title">图片记忆</h3>
                  <span class="panel-count">{{ images.length }} 张</span>
                </div>
                <div class="filters">
                  <el-input v-model="imageDescription" class="keyword-input" placeholder="图片描述（可选）" />
                  <el-button :icon="Upload" @click="chooseImageFile">上传图片</el-button>
                  <input ref="imageInput" type="file" accept="image/*" hidden @change="onImageFile">
                  <el-button :loading="imageLoading" @click="retryImages">重试失败任务</el-button>
                  <el-button :icon="Refresh" :loading="imageLoading" @click="loadImages">刷新</el-button>
                </div>
              </div>
              <div class="meta-grid">
                <span>已记住 <strong>{{ imageStatus?.stats?.asset_count ?? 0 }}</strong> 张</span>
                <span>图片说明 <strong>{{ imageStatus?.stats?.observation_count ?? 0 }}</strong></span>
                <span>关联记忆 <strong>{{ imageStatus?.stats?.link_count ?? 0 }}</strong></span>
                <span>状态 <el-tag size="small">{{ imageStatus?.status || '—' }}</el-tag></span>
                <span v-if="imageStatus?.dimension">维度 {{ imageStatus.dimension }}</span>
                <span v-if="imageStatus?.message">{{ imageStatus.message }}</span>
              </div>
              <div class="image-memory">
                <div v-if="!images.length" class="image-memory-empty">
                  {{ imageOffset > 0 ? '这一页没有更多图片了' : '当前会话没有图片记忆' }}
                </div>
                <div v-else class="image-grid">
                  <button
                    v-for="row in images"
                    :key="row.asset_id"
                    type="button"
                    class="image-card"
                    @click="openImageDetail(row)"
                  >
                    <span class="image-card-frame">
                      <img
                        v-if="row.occurrence_id"
                        :src="amemorixApi.imageUrl(tenant, row.occurrence_id)"
                        alt=""
                        class="image-card-image"
                      >
                      <span
                        v-if="row.occurrence_count > 1"
                        class="image-card-count"
                        title="出现次数"
                      >×{{ row.occurrence_count }}</span>
                    </span>
                    <span class="image-card-meta">
                      <span class="image-card-dims">{{ row.width }} × {{ row.height }}</span>
                      <span class="image-card-bytes">{{ formatBytes(row.byte_size) }}</span>
                    </span>
                  </button>
                </div>
                <div v-if="imageOffset > 0 || images.length >= IMAGE_PAGE_SIZE" class="image-pager">
                  <el-button size="small" :disabled="imageOffset === 0 || imageLoading" @click="stepImagePage(-1)">
                    上一页
                  </el-button>
                  <span>第 {{ imageOffset / IMAGE_PAGE_SIZE + 1 }} 页</span>
                  <el-button
                    size="small"
                    :disabled="images.length < IMAGE_PAGE_SIZE || imageLoading"
                    @click="stepImagePage(1)"
                  >
                    下一页
                  </el-button>
                </div>
              </div>
            </div>

            <div v-if="activeTab === 'timeline'" class="panel">
              <div class="panel-header">
                <h3 class="panel-title">记忆流</h3>
                <el-button :icon="Refresh" :loading="timelineLoading" @click="loadTimeline">刷新</el-button>
              </div>
              <el-table :data="timelineItems" empty-text="没有记忆变更">
                <el-table-column label="时间" width="170">
                  <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
                </el-table-column>
                <el-table-column label="类型" width="96">
                  <template #default="{ row }">
                    <el-tag size="small" effect="plain">
                      {{ categoryLabels[row.category] || row.category }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="事件" width="120">
                  <template #default="{ row }">{{ eventLabels[row.event_type] || row.event_type }}</template>
                </el-table-column>
                <el-table-column label="内容" min-width="320">
                  <template #default="{ row }">
                    <div class="record-title">{{ row.title }}</div>
                    <div class="record-summary">{{ row.summary }}</div>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-if="activeTab === 'transfer'" class="panel">
              <div class="panel-header">
                <h3 class="panel-title">导入导出</h3>
                <PillTabs v-model="transferMode" :items="transferTabs" size="small" />
              </div>

              <template v-if="transferMode === 'bundles'">
                <div class="section-toolbar">
                  <el-switch v-model="bundleGlobal" active-text="全局安装" />
                  <div class="filters">
                    <el-button :icon="Download" @click="exportBundle(false)">导出当前会话</el-button>
                    <el-button :icon="Download" @click="exportBundle(true)">导出全部</el-button>
                    <el-button :icon="Upload" @click="chooseBundleFile">安装记忆包</el-button>
                    <input ref="bundleInput" type="file" accept=".amembundle,.zip" hidden @change="onBundleFile">
                  </div>
                </div>
                <div v-if="bundleDownload" class="config-hint">
                  已生成
                  <a :href="bundleDownload.url" target="_blank" rel="noopener">{{ bundleDownload.name }}</a>
                </div>
                <el-table :data="bundles" empty-text="没有已安装的记忆包">
                  <el-table-column label="名称" min-width="220">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.name || row.package_id }}</div>
                      <div class="record-summary">{{ row.description || row.package_id }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="版本" width="90" prop="version" />
                  <el-table-column label="范围" width="110">
                    <template #default="{ row }">
                      {{ row.scope_type === 'global' ? '全局' : row.chat_id || '会话' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="段落" width="76" prop="paragraph_count" />
                  <el-table-column label="状态" width="90" prop="status" />
                  <el-table-column label="更新时间" width="170">
                    <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="90" fixed="right">
                    <template #default="{ row }">
                      <el-button link type="danger" @click="uninstallBundle(row)">卸载</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </template>

              <template v-else>
                <div class="section-toolbar">
                  <div class="config-hint no-margin">
                    支持 txt / md / json，单任务最多 {{ importSettings?.max_files_per_task ?? '—' }} 个文件，
                    单文件 {{ importSettings?.max_file_size_mb ?? '—' }} MB；粘贴上限
                    {{ importSettings?.max_paste_chars ?? '—' }} 字。
                  </div>
                  <div class="filters">
                    <el-button :icon="Upload" @click="chooseImportFile">上传文件</el-button>
                    <input ref="importInput" type="file" accept=".txt,.md,.json" hidden @change="onImportFile">
                    <el-button :icon="Refresh" :loading="importLoading" @click="loadImports">刷新</el-button>
                  </div>
                </div>
                <div class="import-form">
                  <el-input v-model="importName" class="import-name" placeholder="文件名，如 chat.txt" />
                  <el-switch v-model="importLlm" active-text="启用 LLM 抽取" />
                </div>
                <el-input
                  v-model="importText"
                  type="textarea"
                  :rows="8"
                  placeholder="粘贴要导入的文本"
                  class="config-editor"
                />
                <div class="import-actions">
                  <el-button type="primary" :loading="importLoading" @click="submitImport">
                    创建粘贴导入
                  </el-button>
                </div>
                <h4 class="sub-title">导入任务</h4>
                <el-table :data="importTasks" empty-text="没有导入任务">
                  <el-table-column label="任务" min-width="220">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.task_id }}</div>
                      <div class="record-summary">{{ row.source }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="100" prop="status" />
                  <el-table-column label="步骤" min-width="180" prop="current_step" />
                  <el-table-column label="操作" width="150" fixed="right">
                    <template #default="{ row }">
                      <el-button link type="primary" @click="retryImportTask(row)">重试</el-button>
                      <el-button link type="danger" @click="cancelImportTask(row)">取消</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </template>
            </div>

            <div v-if="activeTab === 'inspect'" class="panel">
              <div class="panel-header">
                <h3 class="panel-title">记忆检修</h3>
                <PillTabs v-model="inspectMode" :items="inspectTabs" size="small" />
              </div>

              <template v-if="inspectMode === 'corrections'">
                <div class="section-toolbar">
                  <div class="config-hint no-margin">
                    展示反馈纠错与批量修正计划；执行后的计划可以在回滚状态允许时撤销。
                  </div>
                  <div class="filters">
                    <el-select v-model="correctionKind" class="type-select" @change="loadCorrections">
                      <el-option label="全部" value="" />
                      <el-option label="反馈纠错" value="feedback" />
                      <el-option label="模糊修正" value="plan" />
                    </el-select>
                    <el-button :icon="Refresh" :loading="correctionLoading" @click="loadCorrections">刷新</el-button>
                  </div>
                </div>
                <el-table :data="correctionItems" empty-text="没有纠正记录">
                  <el-table-column type="expand">
                    <template #default="{ row }">
                      <pre class="json-block">{{ JSON.stringify(row.detail, null, 2) }}</pre>
                    </template>
                  </el-table-column>
                  <el-table-column label="类型" width="96">
                    <template #default="{ row }">
                      <el-tag size="small" effect="plain">{{ row.kind === 'feedback' ? '反馈' : '修正' }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="内容" min-width="280">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.title }}</div>
                      <div class="record-summary">{{ row.decision || '—' }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="110" prop="status" />
                  <el-table-column label="回滚" width="110" prop="rollback_status" />
                  <el-table-column label="时间" width="170">
                    <template #default="{ row }">{{ formatTime(row.updated_at || row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="90" fixed="right">
                    <template #default="{ row }">
                      <el-button link type="primary" @click="rollbackCorrection(row)">回滚</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </template>

              <template v-else-if="inspectMode === 'runtime'">
                <div class="section-toolbar">
                  <div v-if="status" class="memory-summary">
                    <span>段落 {{ status.counts.paragraphs }}</span>
                    <span>实体 {{ status.counts.entities }}</span>
                    <span>关系 {{ status.counts.relations }}</span>
                    <span>情景 {{ status.counts.episodes }}</span>
                    <span>事实 {{ status.counts.facts }}</span>
                    <span>人物 {{ status.counts.persons }}</span>
                    <span>画像 {{ status.counts.profiles }}</span>
                  </div>
                  <div class="filters">
                    <el-button :icon="Refresh" :loading="runtimeLoading" @click="loadRuntime">刷新</el-button>
                    <el-button type="primary" :loading="selfChecking" @click="runSelfCheck">重新自检</el-button>
                  </div>
                </div>

                <div v-if="runtime" class="meta-grid runtime-meta">
                  <span>
                    内核
                    <el-tag :type="runtime.available ? 'success' : 'danger'" size="small">
                      {{ runtime.available ? '运行中' : '未就绪' }}
                    </el-tag>
                  </span>
                  <span>数据目录 <code>{{ runtime.data_dir || '—' }}</code></span>
                  <span>
                    向量重建
                    <el-tag :type="runtime.vector_rebuild_required ? 'warning' : 'success'" size="small">
                      {{ runtime.vector_rebuild_required ? '需要' : '不需要' }}
                    </el-tag>
                  </span>
                  <span>向量池 {{ runtime.vector_pools_effective_mode || '—' }}</span>
                  <span>
                    嵌入降级
                    <el-tag :type="runtime.embedding_degraded ? 'warning' : 'success'" size="small">
                      {{ runtime.embedding_degraded ? '是' : '否' }}
                    </el-tag>
                  </span>
                  <span>
                    待回填 {{ runtime.paragraph_vector_backfill_pending ?? 0 }} · 失败
                    {{ runtime.paragraph_vector_backfill_failed ?? 0 }}
                  </span>
                  <span v-if="runtime.embedding_degraded_reason">原因：{{ runtime.embedding_degraded_reason }}</span>
                  <span v-if="runtime.vector_rebuild_message">提示：{{ runtime.vector_rebuild_message }}</span>
                </div>

                <h4 class="sub-title">记忆来源</h4>
                <el-table :data="sources" empty-text="没有来源">
                  <el-table-column label="来源" min-width="260">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.source_label }}</div>
                      <div class="record-summary">{{ row.source }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="段落数" width="90" prop="count" />
                  <el-table-column label="最近写入" width="180">
                    <template #default="{ row }">{{ formatTime(row.last_updated) }}</template>
                  </el-table-column>
                </el-table>

                <div class="section-toolbar config-head">
                  <h4 class="sub-title no-margin">A_Memorix 配置</h4>
                  <el-button type="primary" :loading="configSaving" @click="saveConfig">保存配置</el-button>
                </div>
                <div class="config-hint">
                  字段与 MaiBot 的 AMemorixConfig 一致；保存后会写回 config/amemorix/config.json，重启 Bot 完全生效。
                </div>
                <el-input v-model="configText" type="textarea" :rows="18" class="config-editor" spellcheck="false" />
              </template>

              <template v-else-if="inspectMode === 'episodes'">
                <div class="section-toolbar">
                  <div class="config-hint no-margin">情景片段由段落自动生成，用于回忆一段对话的前后脉络。</div>
                  <div class="filters">
                    <el-input
                      v-model="episodeQuery"
                      class="keyword-input"
                      clearable
                      placeholder="搜索标题、摘要或参与者"
                      :prefix-icon="Search"
                      @keyup.enter="loadEpisodes"
                      @clear="loadEpisodes"
                    />
                    <el-button @click="loadEpisodes">查询</el-button>
                  </div>
                </div>
                <el-table
                  :data="episodes"
                  empty-text="还没有生成情景片段"
                  @row-click="openRecord"
                >
                  <el-table-column label="标题" min-width="240">
                    <template #default="{ row }">
                      <div class="record-title">{{ row.title }}</div>
                      <div class="record-summary">{{ row.summary }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="参与者" width="180">
                    <template #default="{ row }">
                      {{ (row.metadata.participants || []).join('、') || '—' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="段落" width="70" prop="metadata.paragraph_count" />
                  <el-table-column label="时间" width="170">
                    <template #default="{ row }">{{ formatTime(row.updated_at || row.created_at) }}</template>
                  </el-table-column>
                </el-table>
              </template>

              <template v-else>
                <div class="graph-layout">
                  <div class="graph-column">
                    <div class="section-toolbar">
                      <h4 class="sub-title no-margin">实体（{{ graph.total_nodes }}）</h4>
                      <el-input
                        v-model="graphQuery"
                        class="keyword-input"
                        clearable
                        placeholder="过滤实体"
                        :prefix-icon="Search"
                      />
                    </div>
                    <div class="node-list">
                      <button
                        v-for="node in filteredNodes"
                        :key="node.id"
                        type="button"
                        class="node-chip"
                        :class="{ active: selectedNode === node.name }"
                        @click="openNode(node.name)"
                      >
                        <span>{{ node.name }}</span>
                        <span class="node-count">{{ node.appearance_count }}</span>
                      </button>
                      <div v-if="!filteredNodes.length" class="empty-hint">没有实体</div>
                    </div>
                  </div>
                  <div class="graph-detail">
                    <div class="section-toolbar">
                      <h4 class="sub-title no-margin">{{ selectedNode || '节点详情' }}</h4>
                    </div>
                    <div v-if="nodeLoading" class="loading-block">加载中…</div>
                    <template v-else-if="nodeDetail">
                      <h4 class="sub-title">相关关系（{{ nodeDetail.relations.length }}）</h4>
                      <el-table :data="nodeDetail.relations" size="small" empty-text="没有关系">
                        <el-table-column label="主体" width="120" prop="subject" />
                        <el-table-column label="谓词" width="120" prop="predicate" />
                        <el-table-column label="客体" min-width="120" prop="object" />
                        <el-table-column label="置信度" width="90">
                          <template #default="{ row }">{{ Number(row.confidence || 0).toFixed(2) }}</template>
                        </el-table-column>
                      </el-table>
                      <h4 class="sub-title">证据段落（{{ nodeDetail.paragraphs.length }}）</h4>
                      <div class="paragraph-list">
                        <div
                          v-for="paragraph in nodeDetail.paragraphs"
                          :key="paragraph.hash"
                          class="paragraph-item"
                        >
                          <div class="paragraph-content">{{ paragraph.content }}</div>
                          <div class="paragraph-meta">{{ formatTime(paragraph.created_at) }}</div>
                        </div>
                        <div v-if="!nodeDetail.paragraphs.length" class="empty-hint">没有证据段落</div>
                      </div>
                    </template>
                    <div v-else class="empty-hint">选择左侧实体查看关系与证据</div>
                  </div>
                </div>
              </template>
            </div>

            <div v-if="activeTab === 'erase'" class="panel">
              <div class="panel-header">
                <h3 class="panel-title">记忆抹除</h3>
                <div class="filters">
                  <el-button :icon="Refresh" :loading="recycleLoading" @click="loadRecycle">刷新</el-button>
                  <el-button type="danger" plain @click="purgeRecycle">永久清理</el-button>
                </div>
              </div>
              <div class="config-hint">
                展示当前会话可恢复的删除操作与已撤回事实。永久清理会跨会话清理超过 24 小时的回收记录。
              </div>
              <el-table :data="recycleItems" empty-text="回收站为空">
                <el-table-column label="类型" width="86">
                  <template #default="{ row }">
                    <el-tag size="small" effect="plain">{{ row.kind === 'fact' ? '事实' : '删除' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="内容" min-width="300">
                  <template #default="{ row }">
                    <div class="record-title">{{ row.title }}</div>
                    <div class="record-summary">{{ row.summary }}</div>
                  </template>
                </el-table-column>
                <el-table-column label="条目" width="76" prop="count" />
                <el-table-column label="状态" width="90" prop="status" />
                <el-table-column label="删除时间" width="170">
                  <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="90" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" @click="restoreRecycle(row)">恢复</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </Transition>
      </section>
    </div>

    <el-dialog v-model="imageDetailVisible" title="图片记忆" width="760" align-center class="image-detail-dialog">
      <div class="image-detail">
        <div v-if="imageDetailLoading" class="image-detail-loading">正在加载图片详情…</div>
        <template v-else>
          <div class="image-detail-media">
            <el-image
              v-if="imageDetailOccurrence"
              :src="amemorixApi.imageUrl(tenant, imageDetailOccurrence.occurrence_id)"
              :preview-src-list="[amemorixApi.imageUrl(tenant, imageDetailOccurrence.occurrence_id)]"
              preview-teleported
              fit="contain"
              class="image-detail-thumb"
            />
          </div>
          <div v-if="imageDetail" class="image-detail-body">
            <section class="detail-section">
              <h4 class="detail-title">文件信息</h4>
              <div class="detail-hash">{{ imageDetail.asset.content_hash }}</div>
              <div class="detail-meta">
                {{ imageDetail.asset.mime_type }} ·
                {{ formatBytes(imageDetail.asset.byte_size) }} ·
                {{ imageDetail.asset.width }}×{{ imageDetail.asset.height }}
              </div>
            </section>

            <section class="detail-section">
              <h4 class="detail-title">图片来自哪里</h4>
              <div class="detail-card">
                <div class="detail-card-head">
                  <span class="detail-card-main">
                    {{ imageDetailOccurrence?.chat_name || chatLabel(imageDetailOccurrence?.chat_id || tenant) }}
                    <el-tag v-if="imageDetailOccurrences.length > 1" size="small" effect="plain">
                      出现 {{ imageDetailOccurrences.length }} 次
                    </el-tag>
                  </span>
                  <el-button
                    v-if="imageDetailOccurrence"
                    link
                    type="danger"
                    aria-label="删除这张图片"
                    @click="removeImageFromDetail"
                  >
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
                <div class="detail-card-time">
                  {{ formatEpoch(imageDetailOccurrence?.occurred_at || imageDetailOccurrence?.created_at) }}
                </div>
                <details v-if="imageDetailOccurrences.length" class="detail-records">
                  <summary>来源记录信息</summary>
                  <div v-for="item in imageDetailOccurrences" :key="item.occurrence_id" class="detail-record">
                    <span>{{ imageSourceLabels[item.source_kind] || item.source_kind }}</span>
                    <span class="detail-record-time">{{ formatEpoch(item.occurred_at || item.created_at) }}</span>
                    <span v-if="item.message_id" class="detail-record-msg">消息 {{ item.message_id }}</span>
                  </div>
                </details>
              </div>
            </section>

            <section class="detail-section">
              <h4 class="detail-title">记忆中的说明</h4>
              <div v-for="item in imageDetailObservations" :key="item.observation_id" class="detail-observation">
                <div class="detail-observation-text">{{ item.text }}</div>
                <div class="detail-observation-meta">
                  <el-tag size="small" effect="plain">
                    {{ observationLabel(item.source_kind, item.confirm_status) }}
                  </el-tag>
                </div>
              </div>
              <div v-if="!imageDetailObservations.length" class="detail-empty">还没有记录说明</div>
            </section>

            <section class="detail-section">
              <h4 class="detail-title">关联记忆</h4>
              <div v-for="item in imageDetailLinks" :key="item.link_id" class="detail-link">
                <el-tag size="small">{{ typeLabels[item.target_type] || item.target_type }}</el-tag>
                <span class="detail-link-text">{{ item.memory?.content }}</span>
              </div>
              <div v-if="!imageDetailLinks.length" class="detail-empty">暂无关联记忆</div>
            </section>
          </div>
        </template>
      </div>
    </el-dialog>

    <el-dialog v-model="correctionDialog" title="修正所选记忆" width="680" align-center>
      <div class="correction-head">
        <span>已选 {{ selectedTargets.length }} 条</span>
        <div class="correction-targets">
          <el-tag v-for="target in selectedTargets" :key="target.id" size="small" effect="plain">
            {{ typeLabels[target.type] }} · {{ target.id.slice(0, 8) }}
          </el-tag>
        </div>
      </div>
      <el-input
        v-model="correctionRequest"
        type="textarea"
        :rows="4"
        placeholder="描述要怎样修改，例如：把出生地改为杭州，并补上时间"
      />
      <div class="form-hint">
        AI 会根据所选记忆生成修改计划，确认后才写入；只支持段落与关系。
      </div>

      <div v-if="correctionPlan" class="plan-box">
        <div class="plan-head">
          <span>修改计划 {{ correctionPlan.plan_id }}</span>
          <el-tag size="small" effect="plain">
            置信度 {{ Number(correctionPlan.plan?.confidence || 0).toFixed(2) }}
          </el-tag>
        </div>
        <div v-if="correctionPlan.preview.reason" class="plan-reason">
          {{ correctionPlan.preview.reason }}
        </div>
        <div v-for="(operation, index) in correctionPlan.preview.operations" :key="index" class="plan-row">
          <el-tag
            size="small"
            :type="operation.action === 'mark_superseded' ? 'warning' : 'success'"
            effect="plain"
          >
            {{ actionLabels[operation.action] || operation.action }}
          </el-tag>
          <div class="plan-body">
            <div class="plan-text">{{ operation.text || operation.hash || '—' }}</div>
            <div v-if="operation.reason" class="plan-reason">{{ operation.reason }}</div>
          </div>
        </div>
        <div class="plan-foot">
          候选 {{ correctionPlan.preview.candidates.length }} 条 · 操作
          {{ correctionPlan.preview.operations.length }} 项
        </div>
      </div>

      <template #footer>
        <el-button @click="correctionDialog = false">取消</el-button>
        <el-button :loading="correctionPreviewing" @click="previewCorrection">生成计划</el-button>
        <el-button
          type="primary"
          :loading="correctionExecuting"
          :disabled="!correctionPlan"
          @click="executeCorrection"
        >
          确认执行
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="recordDrawer" :title="selectedRecord?.title || '记忆详情'" size="560px">
      <div class="drawer-body">
        <template v-if="recordDetail">
          <div class="meta-grid">
            <span>类型 <el-tag size="small">{{ typeLabels[recordDetail.record.type] }}</el-tag></span>
            <span>状态 {{ statusLabels[recordDetail.record.status] || recordDetail.record.status }}</span>
            <span>来源 {{ recordDetail.record.source_label || '—' }}</span>
            <span>更新 {{ formatTime(recordDetail.record.updated_at || recordDetail.record.created_at) }}</span>
          </div>
          <div class="detail-block">
            <h4 class="sub-title">记录内容</h4>
            <pre class="json-block">{{ JSON.stringify(recordDetail.record.metadata, null, 2) }}</pre>
          </div>
          <div class="detail-block">
            <h4 class="sub-title">证据段落（{{ recordDetail.paragraphs.length }}）</h4>
            <div class="paragraph-list">
              <div v-for="paragraph in recordDetail.paragraphs" :key="paragraph.hash" class="paragraph-item">
                <div class="paragraph-content">{{ paragraph.content }}</div>
                <div class="paragraph-meta">{{ formatTime(paragraph.created_at) }}</div>
              </div>
              <div v-if="!recordDetail.paragraphs.length" class="empty-hint">没有证据段落</div>
            </div>
          </div>
          <div v-if="selectedRecord?.type !== 'episode'" class="drawer-actions">
            <el-button type="danger" plain :icon="Delete" @click="removeRecord">
              {{ selectedRecord?.type === 'fact' ? '撤回事实' : '删除记录' }}
            </el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <el-drawer v-model="profileDrawer" :title="selectedProfile?.name || '人物画像'" size="560px">
      <div v-if="profileDetail" class="drawer-body">
        <div class="meta-grid">
          <span>用户 ID {{ profileDetail.person.user_id }}</span>
          <span>平台 {{ profileDetail.person.platform }}</span>
          <span>版本 {{ profileDetail.profile.profile_version }}</span>
          <span>更新 {{ formatTime(profileDetail.profile.updated_at) }}</span>
        </div>
        <div class="detail-block">
          <h4 class="sub-title">别名</h4>
          <div class="tag-row">
            <el-tag v-for="alias in profileDetail.profile.aliases" :key="alias" size="small" effect="plain">
              {{ alias }}
            </el-tag>
            <span v-if="!profileDetail.profile.aliases.length" class="empty-hint">没有别名</span>
          </div>
        </div>
        <div class="detail-block">
          <h4 class="sub-title">画像正文</h4>
          <div class="profile-text">{{ profileDetail.profile.profile_text || '尚未生成画像正文' }}</div>
        </div>
        <div class="detail-block">
          <h4 class="sub-title">人物事实（{{ profileDetail.facts.length }}）</h4>
          <div class="paragraph-list">
            <div v-for="fact in profileDetail.facts" :key="fact.id" class="paragraph-item">
              <div class="paragraph-content">{{ fact.title }}</div>
              <div class="paragraph-meta">
                {{ statusLabels[fact.status] || fact.status }} · {{ formatTime(fact.updated_at) }}
              </div>
            </div>
            <div v-if="!profileDetail.facts.length" class="empty-hint">没有事实</div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.amemorix-view {
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
.query-toolbar,
.query-filters,
.batch-bar,
.query-subhead,
.section-toolbar,
.import-form,
.correction-head,
.correction-targets,
.plan-head,
.plan-row,
.tag-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.panel-header,
.query-toolbar,
.section-toolbar {
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

.filters,
.query-filters,
.correction-targets {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.query-subhead {
  justify-content: space-between;
  flex-wrap: wrap;
  margin: 14px 0 10px;
}

.memory-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.type-select {
  width: 180px;
}

.keyword-input {
  width: 240px;
}

.batch-bar {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.batch-info {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.record-list {
  display: flex;
  flex-direction: column;
  min-height: 120px;
  margin-top: 6px;
}

.record-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 8px;
  border-bottom: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
  }

  &.selected {
    background: var(--color-primary-soft);

    .record-title {
      color: var(--color-primary);
    }
  }
}

.record-check {
  flex: none;
}

.record-type {
  flex: none;
}

.record-content {
  flex: 1;
  min-width: 0;
}

.record-title {
  color: var(--color-text);
  font-weight: 500;
  word-break: break-all;
}

.record-summary {
  margin-top: 2px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.record-source {
  flex: none;
  width: 120px;
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-time {
  flex: none;
  width: 145px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: right;
}

.query-hint {
  margin-top: 10px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.clamp-2 {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.meta-grid code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  word-break: break-all;
}

.runtime-meta {
  margin-bottom: 16px;
}

.section-toolbar {
  margin: 14px 0 10px;
}

.config-head {
  margin-top: 22px;
}

.no-margin {
  margin: 0;
}

.sub-title {
  margin: 18px 0 10px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.graph-layout {
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
  margin-top: 4px;
}

.graph-column {
  min-width: 0;
}

.graph-detail {
  min-width: 0;
}

.node-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: 520px;
  overflow: auto;
}

.node-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-bg-card);
  color: var(--color-text);
  font-size: 12px;
  cursor: pointer;
}

.node-chip:hover,
.node-chip.active {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.node-count {
  color: var(--color-text-secondary);
}

.paragraph-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow: auto;
}

.paragraph-item {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
}

.paragraph-content {
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.paragraph-meta {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.empty-hint {
  padding: 16px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.loading-block {
  min-height: 160px;
}

.import-form {
  margin-bottom: 12px;
}

.import-name {
  width: 240px;
}

.import-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.image-memory {
  margin-top: 14px;
  container-type: inline-size;
}

.image-memory-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 160px;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@container (min-width: 30rem) {
  .image-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@container (min-width: 40rem) {
  .image-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container (min-width: 60rem) {
  .image-grid {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}

@container (min-width: 80rem) {
  .image-grid {
    grid-template-columns: repeat(8, minmax(0, 1fr));
  }
}

.image-card {
  display: block;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-card);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;

  &:hover {
    border-color: var(--color-primary);
    box-shadow: var(--shadow-sm);
  }
}

.image-card-frame {
  position: relative;
  display: block;
  height: 144px;
  background: var(--color-bg-secondary);
}

.image-card-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.image-card-count {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: rgba(17, 24, 26, 0.55);
  color: #fff;
  font-size: 11px;
  line-height: 16px;
  font-variant-numeric: tabular-nums;
}

.image-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.image-card-dims {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.image-card-bytes {
  flex: none;
  white-space: nowrap;
}

.image-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.image-detail {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 20px;
  min-height: 240px;
}

.image-detail-loading {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.image-detail-media {
  align-self: start;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.image-detail-thumb {
  width: 100%;
  height: 260px;
  border-radius: var(--radius-md);
  cursor: zoom-in;
}

.image-detail-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 4px;
}

.detail-title {
  margin: 0 0 6px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.detail-hash {
  color: var(--color-text-secondary);
  font-family: 'Courier New', monospace;
  font-size: 11px;
  word-break: break-all;
}

.detail-meta {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.detail-card {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.detail-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;

  .el-button {
    padding: 0;
    margin-top: -2px;
  }
}

.detail-card-main {
  color: var(--color-text);
  font-weight: 500;
  word-break: break-all;

  .el-tag {
    margin-left: 6px;
  }
}

.detail-card-time {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.detail-records {
  margin-top: 8px;
  font-size: 12px;

  summary {
    color: var(--color-text-secondary);
    cursor: pointer;
  }
}

.detail-record {
  display: flex;
  gap: 10px;
  padding: 4px 0 0 8px;
  color: var(--color-text-secondary);
}

.detail-record-msg {
  margin-left: auto;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  word-break: break-all;
}

.detail-observation {
  padding-left: 10px;
  border-left: 2px solid var(--color-border);

  & + & {
    margin-top: 8px;
  }
}

.detail-observation-text {
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.detail-observation-meta {
  margin-top: 6px;
}

.detail-link {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3px 0;
  font-size: 12px;
}

.detail-link-text {
  color: var(--color-text);
  line-height: 1.5;
}

.detail-empty {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.config-hint {
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.config-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}

.correction-head {
  flex-wrap: wrap;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.correction-targets {
  justify-content: flex-start;
  gap: 6px;
}

.form-hint {
  margin-top: 8px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.plan-box {
  margin-top: 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-secondary);
}

.plan-head {
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text);
  font-size: 12px;
  font-weight: 600;
}

.plan-row {
  align-items: flex-start;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border-light);
}

.plan-body {
  flex: 1;
  min-width: 0;
}

.plan-text {
  color: var(--color-text);
  font-size: 12px;
  line-height: 1.6;
  word-break: break-word;
}

.plan-reason {
  margin-top: 3px;
  color: var(--color-text-tertiary);
  font-size: 11px;
  line-height: 1.5;
  word-break: break-word;
}

.plan-head + .plan-reason {
  margin: 0;
  padding: 10px 14px 0;
}

.plan-foot {
  padding: 10px 14px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-block {
  display: flex;
  flex-direction: column;
}

.json-block {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.profile-text {
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.tag-row {
  flex-wrap: wrap;
  gap: 6px;
}

.drawer-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}

@media (max-width: 1100px) {
  .memory-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 960px) {
  .graph-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .keyword-input {
    width: 100%;
  }

  .record-source,
  .record-time {
    display: none;
  }
}

@media (max-width: 760px) {
  .amemorix-view {
    padding: 16px;
  }
}
</style>
