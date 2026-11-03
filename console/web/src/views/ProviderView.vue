<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import ListPager from '@/components/ListPager.vue'
import { usePagination } from '@/composables/usePagination'
import PillTabs from '@/components/PillTabs.vue'
import { configApi, providerApi } from '@/api'
import type {
  ModelRoutingData,
  ModelRoutingTask,
  ProviderItem,
  ProviderTypeOption,
} from '@/types/api'

interface ProviderForm {
  name: string
  provider_type: string
  type: string
  api_key: string
  base_url: string
  model: string
  timeout: number
  embedding_dim: number | null
  voice: string
}

interface WebServiceDraft {
  name: string
  adapter: string
  base_url: string
  api_key: string
  timeout: number
  search_depth: string
  extract_depth: string
  preview_chars: number
}

const tabs = [
  { key: 'models', label: '模型设置', hint: 'Provider 与服务模型' },
  { key: 'routing', label: '功能分配', hint: '对话、规划、联网使用哪个模型或服务' },
] as const

type TabKey = (typeof tabs)[number]['key']

const capabilityOptions = [
  { label: '对话', value: 'chat' },
  { label: '嵌入', value: 'embedding' },
  { label: '语音识别', value: 'stt' },
  { label: '语音合成', value: 'tts' },
]

const activeTab = ref<TabKey>('models')
const saving = ref(false)
const loaded = ref(false)
const providers = ref<ProviderItem[]>([])
const typeOptions = ref<ProviderTypeOption[]>([])
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)
const form = ref<ProviderForm>(emptyForm())
const selectedProvider = ref('')
const providerQuery = ref('')

const routing = ref<ModelRoutingData | null>(null)
const routingLoading = ref(false)
const routingSaving = ref(false)
const selectedTask = ref('')
const showAdvanced = ref(false)

const webDialogVisible = ref(false)
const webEditingName = ref('')
const webSaving = ref(false)
const webDraft = ref<WebServiceDraft>(emptyWebDraft())

const webAdapters = [
  { label: 'DeepSeek', value: 'deepseek', base_url: 'https://api.deepseek.com' },
  { label: 'Tavily', value: 'tavily', base_url: 'https://api.tavily.com' },
  { label: 'AnySearch', value: 'anysearch', base_url: 'https://api.anysearch.com' },
  { label: 'Exa', value: 'exa', base_url: 'https://api.exa.ai' },
  { label: 'Firecrawl', value: 'firecrawl', base_url: 'https://api.firecrawl.dev' },
]

function emptyWebDraft(): WebServiceDraft {
  return {
    name: '',
    adapter: 'deepseek',
    base_url: '',
    api_key: '',
    timeout: 60,
    search_depth: 'basic',
    extract_depth: 'basic',
    preview_chars: 4000,
  }
}

function emptyForm(): ProviderForm {
  return {
    name: '',
    provider_type: 'chat',
    type: '',
    api_key: '',
    base_url: '',
    model: '',
    timeout: 60,
    embedding_dim: null,
    voice: '',
  }
}

const availableTypeOptions = computed(() =>
  typeOptions.value.filter((item) => item.provider_type === form.value.provider_type),
)

const currentTypeOption = computed(() =>
  availableTypeOptions.value.find((item) => item.type === form.value.type),
)

const baseUrlPlaceholder = computed(() => {
  if (form.value.type === 'vertex') {
    return 'https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}'
  }
  return currentTypeOption.value?.default_base_url || 'https://api.example.com/v1'
})

const defaultProvider = computed(() => providers.value.find((item) => item.is_default)?.name || '')

const visibleProviders = computed(() => {
  let list = providers.value
  if (selectedProvider.value) list = list.filter((item) => item.name === selectedProvider.value)
  const keyword = providerQuery.value.trim().toLowerCase()
  if (keyword) {
    list = list.filter((item) =>
      [item.name, item.model, item.type_label, item.type].some((value) =>
        String(value || '').toLowerCase().includes(keyword),
      ),
    )
  }
  return list
})

const PROVIDER_PAGE_SIZE = 15
const {
  page: providerPage,
  total: providerTotal,
  paged: pagedProviders,
} = usePagination(visibleProviders, PROVIDER_PAGE_SIZE)

const currentTask = computed<ModelRoutingTask | null>(
  () => routing.value?.tasks.find((item) => item.task === selectedTask.value) || null,
)

const taskProviders = computed(() => {
  const capability = currentTask.value?.provider_type || 'chat'
  return (routing.value?.providers || []).filter((item) => item.provider_type === capability)
})

const isAutoCapability = (value?: string) =>
  value === 'stt' || value === 'tts' || value === 'embedding'

const isWebTask = computed(() => currentTask.value?.provider_type === 'web')

const isVisionTask = computed(() => currentTask.value?.task === 'vision')

const assignedWebService = computed(
  () => (routing.value?.web_services || []).find((item) => item.name === currentTask.value?.provider) || null,
)

const webServices = computed(() => routing.value?.web_services || [])

const taskOptions = computed(() =>
  isWebTask.value
    ? webServices.value.map((item) => ({ name: item.name, label: `${item.name} · ${item.adapter}` }))
    : taskProviders.value.map((item) => ({ name: item.name, label: `${item.name} · ${item.model || item.type_label}` })),
)

const visibleTasks = computed(() =>
  (routing.value?.tasks || []).filter((item) => showAdvanced.value || !item.advanced),
)

watch(showAdvanced, () => {
  if (!visibleTasks.value.some((item) => item.task === selectedTask.value)) {
    selectedTask.value = visibleTasks.value[0]?.task || ''
  }
})

const assignedProvider = computed(() => {
  if (isVisionTask.value) return providers.value.find((item) => item.name === currentTask.value?.provider) || null
  const fallback = isAutoCapability(currentTask.value?.provider_type) ? '' : routing.value?.default_provider
  const name = currentTask.value?.provider || fallback || ''
  return providers.value.find((item) => item.name === name) || null
})

const taskFallbackLabel = computed(() => {
  if (isWebTask.value) return '不启用'
  if (isVisionTask.value) return '不启用视觉'
  if (isAutoCapability(currentTask.value?.provider_type)) return '自动选择'
  return `跟随默认${routing.value?.default_provider ? `（${routing.value.default_provider}）` : ''}`
})

const taskHint = computed(() => {
  if (isWebTask.value) return '搜索与网页读取共用这个服务；留空不开放统一联网工具。'
  if (isVisionTask.value)
    return '留空表示不启用视觉：图片理解与表情识别都不会调用模型，不会回落到默认对话模型。请分配一个支持图片输入的 Provider。'
  if (currentTask.value?.provider_type === 'stt')
    return '留空时自动使用第一个语音识别 Provider；这里只决定由哪个 Provider 执行业务。'
  if (currentTask.value?.provider_type === 'tts')
    return '留空时自动使用第一个语音合成 Provider；这里只决定由哪个 Provider 执行业务。'
  if (currentTask.value?.provider_type === 'embedding')
    return '留空时自动使用第一个嵌入 Provider；这里只决定由哪个 Provider 执行业务。'
  return '未单独分配时沿用默认对话模型；这里只决定由哪个 Provider 执行该任务。'
})

const detailRows = computed(() => {
  if (isWebTask.value) {
    const service = assignedWebService.value
    return [
      { label: '接口类型', value: service?.adapter || '—' },
      { label: '超时', value: service ? `${service.timeout} 秒` : '—' },
      { label: '密钥', value: service ? (service.has_api_key ? '已配置' : '未配置') : '—' },
    ]
  }
  const provider = assignedProvider.value
  return [
    { label: '实际模型', value: provider?.model || '—' },
    { label: '接口类型', value: provider?.type_label || '—' },
    { label: '超时', value: provider ? `${provider.timeout} 秒` : '—' },
    { label: '密钥', value: provider ? (provider.has_api_key ? '已配置' : '未配置') : '—' },
  ]
})

const capabilityLabel = (value: string) =>
  value === 'embedding'
    ? '嵌入'
    : value === 'stt'
      ? '语音识别'
      : value === 'tts'
        ? '语音合成'
        : '对话'

const capabilityInitial = (value: string) =>
  value === 'embedding' ? 'E' : value === 'stt' ? 'S' : value === 'tts' ? 'T' : 'C'

const applyTypeDefaults = (option?: ProviderTypeOption) => {
  if (!option) return
  form.value.base_url = option.default_base_url || ''
  form.value.timeout = option.default_timeout || 60
  form.value.embedding_dim =
    form.value.provider_type === 'embedding' ? (option.default_embedding_dim ?? 1536) : null
  form.value.voice = form.value.provider_type === 'tts' ? (option.default_voice ?? 'alloy') : ''
}

const firstTypeOption = (capability: string) =>
  typeOptions.value.find((item) => item.provider_type === capability)

const hasCapability = (capability: string) =>
  typeOptions.value.some((item) => item.provider_type === capability)

const onCapabilityChange = () => {
  const option = firstTypeOption(form.value.provider_type)
  form.value.type = option?.type || ''
  applyTypeDefaults(option)
}

const onTypeChange = () => {
  applyTypeDefaults(currentTypeOption.value)
}

const loadProviders = async () => {
  try {
    const res = await providerApi.list()
    if (!res.success || !res.data) {
      ElMessage.error(res.message || '加载 Provider 失败')
      return
    }
    providers.value = res.data.providers || []
    typeOptions.value = res.data.types || []
    if (selectedProvider.value && !providers.value.some((item) => item.name === selectedProvider.value)) {
      selectedProvider.value = ''
    }
    if (routing.value) await loadRouting()
  } catch (e) {
    console.error(e)
    ElMessage.error('加载 Provider 失败，请稍后再试')
  } finally {
    loaded.value = true
  }
}

const loadRouting = async () => {
  routingLoading.value = true
  try {
    const res = await providerApi.routing()
    if (!res.success || !res.data) {
      ElMessage.error(res.message || '加载功能分配失败')
      return
    }
    routing.value = res.data
    if (!res.data.tasks.some((item) => item.task === selectedTask.value)) {
      selectedTask.value = res.data.tasks[0]?.task || ''
    }
  } catch (e) {
    console.error(e)
    ElMessage.error('加载功能分配失败，请稍后再试')
  } finally {
    routingLoading.value = false
  }
}

const webBaseUrlPlaceholder = computed(
  () => webAdapters.find((item) => item.value === webDraft.value.adapter)?.base_url || '',
)

const webNameLocked = computed(
  () => !!webEditingName.value && webEditingName.value === currentTask.value?.provider,
)

const openWebDialog = async (name = '') => {
  try {
    const configs = await configApi.getAll()
    const chat = configs.system?.chat || {}
    const web = chat.web || {}
    const entry = (web.services || []).find((item: any) => item?.name === name) || {}
    webDraft.value = {
      name,
      adapter: String(entry.adapter || 'deepseek'),
      base_url: String(entry.base_url || ''),
      api_key: String(entry.api_key || ''),
      timeout: Number(entry.timeout ?? 60),
      search_depth: String(entry.search_depth || 'basic'),
      extract_depth: String(entry.extract_depth || 'basic'),
      preview_chars: Number(web.preview_chars ?? 4000),
    }
    webEditingName.value = name
    webDialogVisible.value = true
  } catch (e) {
    console.error(e)
    ElMessage.error('加载联网服务失败，请稍后再试')
  }
}

const persistWebServices = async (mutate: (services: any[], routing: Record<string, any>) => void) => {
  webSaving.value = true
  try {
    const configs = await configApi.getAll()
    if (!configs.system) configs.system = {}
    const chat = configs.system.chat || (configs.system.chat = {})
    const web = chat.web || (chat.web = {})
    const services = Array.isArray(web.services) ? web.services : (web.services = [])
    const routingMap = chat.model_routing || (chat.model_routing = {})
    mutate(services, routingMap)
    web.preview_chars = webDraft.value.preview_chars
    await configApi.save(configs)
    webDialogVisible.value = false
    ElMessage.success('联网服务已保存')
    await loadRouting()
  } catch (e: any) {
    if (e?.response?.status === 409) ElMessage.error('配置已在其他页面被修改，请刷新后重试')
    else ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || e))
  } finally {
    webSaving.value = false
  }
}

const saveWebService = async () => {
  const draft = webDraft.value
  const name = draft.name.trim()
  if (!name) return ElMessage.error('请填写服务名称')
  if (!draft.api_key.trim()) return ElMessage.error('请填写 API Key')
  if (!Number.isFinite(draft.timeout) || draft.timeout < 1 || draft.timeout > 300)
    return ElMessage.error('超时时间应大于 0 且不超过 300 秒')
  if (draft.base_url.trim()) {
    try {
      const url = new URL(draft.base_url.trim())
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
        throw new Error('invalid')
    } catch {
      return ElMessage.error('API 地址须为 HTTP(S) 地址，不能包含用户名、密码、查询参数或片段')
    }
  }
  const editing = webEditingName.value
  if (name !== editing && webServices.value.some((item) => item.name === name))
    return ElMessage.error('服务名称已存在')
  await persistWebServices((services, modelRouting) => {
    const entry = {
      name,
      adapter: draft.adapter,
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim(),
      timeout: draft.timeout,
      search_depth: draft.search_depth,
      extract_depth: draft.extract_depth,
    }
    const index = services.findIndex((item: any) => item?.name === editing)
    if (index >= 0) services[index] = entry
    else services.push(entry)
    if (editing && editing !== name && modelRouting.web_search === editing) modelRouting.web_search = name
  })
}

const deleteWebService = async () => {
  const editing = webEditingName.value
  if (!editing) return
  try {
    await ElMessageBox.confirm(`删除联网服务「${editing}」？正在使用时会被取消选用。`, '删除联网服务', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await persistWebServices((services, modelRouting) => {
    const index = services.findIndex((item: any) => item?.name === editing)
    if (index >= 0) services.splice(index, 1)
    if (modelRouting.web_search === editing) modelRouting.web_search = ''
  })
}

const openCreateDialog = () => {
  if (!typeOptions.value.length) {
    ElMessage.warning('没有可用的 Provider 类型，请先安装对应 SDK')
    return
  }
  editingName.value = null
  const option = firstTypeOption('chat') || typeOptions.value[0]
  form.value = {
    ...emptyForm(),
    provider_type: option?.provider_type || 'chat',
    type: option?.type || '',
  }
  applyTypeDefaults(option)
  dialogVisible.value = true
}

const openEditDialog = (item: ProviderItem) => {
  editingName.value = item.name
  form.value = {
    name: item.name,
    provider_type: item.provider_type,
    type: item.type,
    api_key: '',
    base_url: item.base_url,
    model: item.model,
    timeout: item.timeout || 60,
    embedding_dim: item.embedding_dim ?? null,
    voice: item.voice || '',
  }
  dialogVisible.value = true
}

const saveProvider = async () => {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入 Provider 名称')
    return
  }
  if (!form.value.type) {
    ElMessage.warning('请选择接口类型')
    return
  }
  if (!editingName.value && !form.value.api_key.trim()) {
    ElMessage.warning('请输入 API Key')
    return
  }
  if (!form.value.base_url.trim()) {
    ElMessage.warning('请输入 Base URL')
    return
  }
  if (!form.value.model.trim()) {
    ElMessage.warning('请输入模型名称')
    return
  }
  if (!form.value.timeout || form.value.timeout <= 0) {
    ElMessage.warning('超时时间需大于 0')
    return
  }
  if (
    form.value.provider_type === 'embedding' &&
    (!form.value.embedding_dim || form.value.embedding_dim <= 0)
  ) {
    ElMessage.warning('请输入向量维度')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.value.name.trim(),
      type: form.value.type,
      provider_type: form.value.provider_type,
      api_key: form.value.api_key.trim(),
      base_url: form.value.base_url.trim(),
      model: form.value.model.trim(),
      timeout: form.value.timeout,
      embedding_dim: form.value.embedding_dim,
      voice: form.value.voice.trim(),
    }
    const res = editingName.value
      ? await providerApi.update(editingName.value, payload)
      : await providerApi.create(payload)
    if (!res.success) {
      ElMessage.error(res.message || '保存失败')
      return
    }
    dialogVisible.value = false
    ElMessage.success(editingName.value ? '已保存' : '已添加')
    await loadProviders()
  } catch (e) {
    console.error(e)
    ElMessage.error('保存失败，请稍后再试')
  } finally {
    saving.value = false
  }
}

const setDefault = async (item: ProviderItem) => {
  try {
    const res = await providerApi.setDefault(item.name)
    if (!res.success) {
      ElMessage.error(res.message || '设置失败')
      return
    }
    ElMessage.success(`已将「${item.name}」设为默认模型`)
    await loadProviders()
  } catch (e) {
    console.error(e)
    ElMessage.error('设置失败，请稍后再试')
  }
}

const onUseChange = (item: ProviderItem, checked: boolean) => {
  if (!checked) {
    if (item.is_default) ElMessage.info('默认模型不能取消，直接勾选其他模型即可切换')
    return
  }
  if (!item.is_default) void setDefault(item)
}

const deleteProvider = async (item: ProviderItem) => {
  const suffix = item.is_default ? '\n删除后会自动切换到其他对话 Provider。' : ''
  try {
    await ElMessageBox.confirm(`确认删除 Provider "${item.name}"?${suffix}`, '删除 Provider', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const res = await providerApi.remove(item.name)
    if (!res.success) {
      ElMessage.error(res.message || '删除失败')
      return
    }
    ElMessage.success(`已删除「${item.name}」`)
    await loadProviders()
  } catch (e) {
    console.error(e)
    ElMessage.error('删除失败，请稍后再试')
  }
}

const updateTaskProvider = async (value: string) => {
  if (!currentTask.value || routingSaving.value) return
  routingSaving.value = true
  try {
    const res = await providerApi.setRouting(currentTask.value.task, value)
    if (!res.success) {
      ElMessage.error(res.message || '保存失败')
      return
    }
    ElMessage.success(
      value
        ? `已分配给「${value}」`
        : isWebTask.value
          ? '已关闭联网工具'
          : isVisionTask.value
            ? '已关闭视觉识别'
            : '已改为跟随默认模型',
    )
    await loadRouting()
  } catch (e) {
    console.error(e)
    ElMessage.error('保存失败，请稍后再试')
  } finally {
    routingSaving.value = false
  }
}

watch(activeTab, (value) => {
  if (value === 'routing' && !routing.value) void loadRouting()
})

onMounted(() => {
  void loadProviders()
})
</script>

<template>
  <div class="provider-view">
    <PageHeader
      title="模型配置"
      :subtitle="`共 ${providers.length} 个 Provider · 默认对话：${defaultProvider || '未设置'}`"
    >
      <template #actions>
        <el-button :icon="Refresh" @click="loadProviders">刷新</el-button>
        <el-button type="primary" :icon="Plus" :disabled="!typeOptions.length" @click="openCreateDialog">
          添加 Provider
        </el-button>
      </template>
    </PageHeader>

    <PillTabs v-model="activeTab" :items="tabs" class="config-tabs" />

    <Transition name="tab-swap" mode="out-in">
      <div v-if="activeTab === 'models'" key="models" class="models-layout">
        <aside class="vendor-side">
          <div class="vendor-head">
            <span class="vendor-title">模型服务商</span>
            <el-button link type="primary" :icon="Plus" @click="openCreateDialog">添加</el-button>
          </div>
          <button
            type="button"
            class="vendor-item"
            :class="{ active: !selectedProvider }"
            @click="selectedProvider = ''"
          >
            <span class="vendor-avatar all">全</span>
            <span class="vendor-body">
              <span class="vendor-name">全部模型</span>
              <span class="vendor-meta">{{ providers.length }} 个 Provider</span>
            </span>
          </button>
          <button
            v-for="item in providers"
            :key="item.name"
            type="button"
            class="vendor-item"
            :class="{ active: selectedProvider === item.name }"
            :title="item.name"
            @click="selectedProvider = item.name"
          >
            <span class="vendor-avatar" :class="item.provider_type">
              {{ capabilityInitial(item.provider_type) }}
            </span>
            <span class="vendor-body">
              <span class="vendor-name">
                {{ item.name }}
                <span v-if="item.is_default" class="vendor-default">默认</span>
              </span>
              <span class="vendor-meta">{{ item.type_label }} · {{ item.model }}</span>
            </span>
          </button>
        </aside>

        <section class="models-main">
          <div class="models-toolbar">
            <el-input
              v-model="providerQuery"
              class="search-input"
              clearable
              placeholder="搜索 Provider 或模型"
              :prefix-icon="Search"
            />
            <span class="toolbar-hint">{{ visibleProviders.length }} 个模型</span>
          </div>

          <el-table v-if="loaded && (visibleProviders.length || providerQuery)" :data="pagedProviders" row-key="name">
            <el-table-column label="模型名称" min-width="220">
              <template #default="{ row }">
                <div class="model-name">
                  {{ row.name }}
                  <el-tag v-if="row.is_default" size="small" type="warning" effect="plain">默认</el-tag>
                </div>
                <div class="model-sub">{{ row.type_label }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="model" label="模型" min-width="180" show-overflow-tooltip />
            <el-table-column label="能力" width="84">
              <template #default="{ row }">
                <el-tag size="small" :type="row.provider_type === 'chat' ? 'success' : 'info'" effect="plain">
                  {{ capabilityLabel(row.provider_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="超时" width="84">
              <template #default="{ row }">{{ row.timeout }}s</template>
            </el-table-column>
            <el-table-column label="密钥" width="84">
              <template #default="{ row }">
                <span :class="{ missing: !row.has_api_key }">{{ row.has_api_key ? '已配置' : '未配置' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="使用" width="76">
              <template #default="{ row }">
                <el-tooltip
                  :disabled="row.provider_type === 'chat'"
                  content="只有对话 Provider 可以作为默认模型"
                  placement="top"
                >
                  <el-checkbox
                    :model-value="row.is_default"
                    :disabled="row.provider_type !== 'chat'"
                    @change="(checked: boolean | string | number) => onUseChange(row, Boolean(checked))"
                  />
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEditDialog(row)">编辑</el-button>
                <el-button link type="danger" @click="deleteProvider(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div v-else-if="loaded" class="empty-state">
            <div class="empty-icon">🔌</div>
            <div class="empty-title">还没有 Provider</div>
            <p class="empty-desc">
              {{
                typeOptions.length
                  ? '添加一个模型服务，AI 对话才能工作。'
                  : '未检测到可用的 Provider SDK，请先安装依赖。'
              }}
            </p>
            <el-button v-if="typeOptions.length" type="primary" @click="openCreateDialog">添加第一个 Provider</el-button>
          </div>
          <ListPager v-model:page="providerPage" :total="providerTotal" :page-size="PROVIDER_PAGE_SIZE" />
        </section>
      </div>

      <div v-else-if="activeTab === 'routing'" key="routing" class="routing-layout">
        <aside class="task-side">
          <div class="vendor-head">
            <span class="vendor-title">功能任务</span>
            <div class="task-head-actions">
              <span class="advanced-label">高级</span>
              <el-switch v-model="showAdvanced" size="small" />
              <el-button link type="primary" :icon="Refresh" :loading="routingLoading" @click="loadRouting">
                刷新
              </el-button>
            </div>
          </div>
          <button
            v-for="task in visibleTasks"
            :key="task.task"
            type="button"
            class="task-item"
            :class="{ active: selectedTask === task.task }"
            @click="selectedTask = task.task"
          >
            <span class="task-name">
              {{ task.label }}
              <span v-if="task.advanced" class="task-advanced">高级</span>
            </span>
            <span class="task-meta">
              {{
                task.provider ||
                (task.provider_type === 'web'
                  ? '未启用'
                  : isAutoCapability(task.provider_type)
                    ? '自动选择'
                    : `跟随默认 · ${routing?.default_provider || '未设置'}`)
              }}
            </span>
          </button>
          <div v-if="!visibleTasks.length" class="task-empty">暂无任务</div>
        </aside>

        <section class="panel task-main">
          <template v-if="currentTask">
            <div class="task-title-row">
              <div>
                <h3 class="panel-title">{{ currentTask.label }}</h3>
                <p class="task-desc">{{ currentTask.description }}</p>
              </div>
              <el-tag size="small" :type="currentTask.provider ? 'success' : 'info'">
                {{
                  currentTask.provider
                    ? isWebTask
                      ? '已启用'
                      : '单独分配'
                    : isWebTask || isVisionTask
                      ? '未启用'
                      : isAutoCapability(currentTask.provider_type)
                        ? '自动选择'
                        : '跟随默认'
                }}
              </el-tag>
            </div>

            <div class="form-block">
              <div class="form-label">{{ isWebTask ? '搜索服务' : '分配模型' }}</div>
              <el-select
                :model-value="currentTask.provider"
                class="provider-select"
                :loading="routingSaving"
                :placeholder="isWebTask ? '选择已添加的联网服务' : ''"
                @change="(value: string) => updateTaskProvider(value)"
              >
                <el-option value="" :label="taskFallbackLabel" />
                <el-option
                  v-for="option in taskOptions"
                  :key="option.name"
                  :value="option.name"
                  :label="option.label"
                />
              </el-select>
              <div class="form-hint">
                {{ taskOptions.length || !isWebTask ? taskHint : '还没有联网服务，请先添加连接配置。' }}
              </div>
              <div v-if="isWebTask" class="web-actions">
                <el-button @click="openWebDialog()">添加联网搜索</el-button>
                <el-button v-if="currentTask.provider" text type="primary" @click="openWebDialog(currentTask.provider)">
                  编辑「{{ currentTask.provider }}」
                </el-button>
              </div>
            </div>

            <div class="detail-grid">
              <div v-for="row in detailRows" :key="row.label" class="detail-item">
                <span class="detail-label">{{ row.label }}</span>
                <span class="detail-value">{{ row.value }}</span>
              </div>
            </div>

          </template>
          <el-empty v-else description="暂无功能任务" />
        </section>
      </div>
    </Transition>

    <el-dialog
      v-model="dialogVisible"
      :title="editingName ? '编辑 Provider' : '添加 Provider'"
      width="620"
      align-center
    >
      <el-form :model="form" label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" :disabled="!!editingName" placeholder="例如：main" />
          <div class="form-hint">
            {{ editingName ? '名称创建后不可修改。' : '支持中英文、数字、下划线、点和短横线。' }}
          </div>
        </el-form-item>

        <el-form-item label="能力">
          <el-radio-group v-model="form.provider_type" @change="onCapabilityChange">
            <el-radio
              v-for="opt in capabilityOptions"
              :key="opt.value"
              :value="opt.value"
              :disabled="!hasCapability(opt.value)"
              border
            >
              {{ opt.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="接口类型">
          <el-select v-model="form.type" style="width: 100%" @change="onTypeChange">
            <el-option v-for="opt in availableTypeOptions" :key="opt.type" :label="opt.label" :value="opt.type" />
          </el-select>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editingName ? '留空则保持原密钥不变' : 'sk-...'"
          />
        </el-form-item>

        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" :placeholder="baseUrlPlaceholder" />
          <div v-if="form.type === 'vertex'" class="form-hint">
            Vertex 需填写到 projects/{project}/locations/{region}。
          </div>
        </el-form-item>

        <el-form-item label="模型">
          <el-input v-model="form.model" placeholder="例如：gpt-4o-mini" />
        </el-form-item>

        <div class="form-row">
          <el-form-item label="超时时间（秒）">
            <el-input-number
              v-model="form.timeout"
              :min="1"
              :max="600"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item v-if="form.provider_type === 'embedding'" label="向量维度">
            <el-input-number
              v-model="form.embedding_dim"
              :min="1"
              :max="65536"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item v-if="form.provider_type === 'tts'" label="音色">
            <el-input v-model="form.voice" placeholder="alloy" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveProvider">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="webDialogVisible"
      :title="webEditingName ? '编辑联网搜索' : '添加联网搜索'"
      width="460"
      align-center
      class="web-service-dialog"
    >
      <el-form label-position="top" :model="webDraft">
        <el-form-item label="服务名称">
          <el-input v-model="webDraft.name" :disabled="webNameLocked" placeholder="例如：日常搜索" />
        </el-form-item>
        <el-form-item label="服务商">
          <el-select v-model="webDraft.adapter" style="width: 100%">
            <el-option v-for="item in webAdapters" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="webDraft.api_key"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="粘贴服务密钥"
          />
        </el-form-item>
        <el-form-item label="API 地址">
          <el-input v-model="webDraft.base_url" :placeholder="`${webBaseUrlPlaceholder}（留空用官方地址）`" />
        </el-form-item>
        <div v-if="webNameLocked" class="form-hint web-locked-hint">
          该服务正在用于联网搜索；改名或删除前，请先在功能分配中取消选用。
        </div>
        <details class="web-advanced">
          <summary class="web-advanced-title">高级选项</summary>
          <div class="web-advanced-grid">
            <el-form-item label="请求超时（秒）">
              <el-input-number v-model="webDraft.timeout" :min="1" :max="300" controls-position="right" />
            </el-form-item>
            <el-form-item label="内容预览字数">
              <el-input-number
                v-model="webDraft.preview_chars"
                :min="500"
                :max="12000"
                :step="500"
                controls-position="right"
              />
            </el-form-item>
            <el-form-item v-if="webDraft.adapter === 'tavily'" label="搜索深度">
              <el-select v-model="webDraft.search_depth" style="width: 100%">
                <el-option label="基础" value="basic" />
                <el-option label="高级" value="advanced" />
                <el-option label="快速" value="fast" />
                <el-option label="极速" value="ultra-fast" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="webDraft.adapter === 'tavily'" label="提取深度">
              <el-select v-model="webDraft.extract_depth" style="width: 100%">
                <el-option label="基础" value="basic" />
                <el-option label="高级" value="advanced" />
              </el-select>
            </el-form-item>
          </div>
        </details>
      </el-form>
      <template #footer>
        <div class="web-dialog-footer">
          <el-button v-if="webEditingName" type="danger" plain @click="deleteWebService">删除</el-button>
          <span v-else />
          <div class="web-dialog-footer-right">
            <el-button @click="webDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="webSaving" @click="saveWebService">保存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.provider-view {
  padding: 24px 28px 32px;
}

.config-tabs {
  width: fit-content;
  max-width: 100%;
  margin-bottom: 18px;
}

.models-layout,
.routing-layout {
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.vendor-side,
.task-side {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

.vendor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 6px 10px;
}

.vendor-title {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.vendor-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
  }

  &.active {
    background: var(--color-primary-soft);

    .vendor-name {
      color: var(--color-primary);
    }
  }
}

.vendor-avatar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;

  &.chat {
    background: var(--color-primary-soft);
    color: var(--color-primary);
  }

  &.all {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }
}

.vendor-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.vendor-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vendor-default {
  margin-left: 4px;
  color: var(--color-primary);
  font-size: 10px;
  font-weight: 500;
}

.vendor-meta {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.models-main {
  min-width: 0;
}

.models-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.search-input {
  width: 280px;
}

.toolbar-hint {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.model-name {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
  font-weight: 600;
  word-break: break-all;
}

.model-sub {
  margin-top: 2px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.missing {
  color: var(--color-danger);
}

.empty-state {
  padding: 72px 24px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-bg-card);
  text-align: center;
}

.empty-icon {
  margin-bottom: 12px;
  font-size: 40px;
}

.empty-title {
  color: var(--color-text);
  font-size: 17px;
  font-weight: 700;
}

.empty-desc {
  margin: 8px 0 20px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.task-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  cursor: pointer;

  &:hover {
    background: var(--color-bg-tertiary);
  }

  &.active {
    background: var(--color-primary-soft);

    .task-name {
      color: var(--color-primary);
    }
  }
}

.task-name {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.task-advanced {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-weight: 500;
}

.task-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.advanced-label {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.task-meta {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-empty {
  padding: 24px 8px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.task-main {
  min-height: 360px;
}

.web-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.web-advanced {
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);

  &[open] {
    padding-bottom: 2px;
  }
}

.web-advanced-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  list-style: none;

  &::-webkit-details-marker {
    display: none;
  }

  &::after {
    content: '';
    width: 6px;
    height: 6px;
    border-right: 1.5px solid currentColor;
    border-bottom: 1.5px solid currentColor;
    transform: rotate(45deg);
    transition: transform 0.15s ease;
  }
}

.web-advanced[open] .web-advanced-title::after {
  transform: rotate(-135deg);
}

.web-advanced-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}

.web-locked-hint {
  margin: -6px 0 12px;
}

.task-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.task-desc {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.form-block {
  margin-bottom: 20px;
}

.form-label {
  margin-bottom: 8px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.provider-select {
  width: 100%;
  max-width: 420px;
}

.form-hint {
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
  padding: 14px 16px;
  border-radius: var(--radius-lg);
  background: var(--color-bg-secondary);
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.detail-label {
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.detail-value {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
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

@media (max-width: 960px) {
  .models-layout,
  .routing-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .provider-view {
    padding: 16px;
  }

  .search-input {
    width: 100%;
  }

  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>

<style lang="scss">
.web-service-dialog {
  .el-dialog__header {
    padding: 20px 24px 0;
  }

  .el-dialog__title {
    font-size: 16px;
    font-weight: 600;
  }

  .el-dialog__body {
    padding: 14px 24px 0;
  }

  .el-dialog__footer {
    padding: 6px 24px 20px;
  }

  .el-form-item {
    margin-bottom: 12px;
  }

  .el-form-item__label {
    padding-bottom: 4px !important;
    color: var(--color-text);
    font-size: 13px;
    line-height: 20px;
  }

  .el-input-number {
    width: 100%;
  }

  .web-dialog-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .web-dialog-footer-right {
    display: flex;
    gap: 8px;
  }
}
</style>
