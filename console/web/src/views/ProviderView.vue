<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import PillTabs from '@/components/PillTabs.vue'
import { providerApi } from '@/api'
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

const tabs = [
  { key: 'models', label: '模型设置', hint: 'Provider 与服务模型' },
  { key: 'routing', label: '功能分配', hint: '对话、规划与抽取使用哪个模型' },
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

const currentTask = computed<ModelRoutingTask | null>(
  () => routing.value?.tasks.find((item) => item.task === selectedTask.value) || null,
)

const taskProviders = computed(() => {
  const capability = currentTask.value?.provider_type || 'chat'
  return (routing.value?.providers || []).filter((item) => item.provider_type === capability)
})

const isAutoCapability = (value?: string) =>
  value === 'stt' || value === 'tts' || value === 'embedding'

const assignedProvider = computed(() => {
  const fallback = isAutoCapability(currentTask.value?.provider_type) ? '' : routing.value?.default_provider
  const name = currentTask.value?.provider || fallback || ''
  return providers.value.find((item) => item.name === name) || null
})

const taskFallbackLabel = computed(() => {
  if (isAutoCapability(currentTask.value?.provider_type)) return '自动选择'
  return `跟随默认${routing.value?.default_provider ? `（${routing.value.default_provider}）` : ''}`
})

const taskHint = computed(() => {
  if (currentTask.value?.provider_type === 'stt')
    return '留空时自动使用第一个语音识别 Provider；这里只决定由哪个 Provider 执行业务。'
  if (currentTask.value?.provider_type === 'tts')
    return '留空时自动使用第一个语音合成 Provider；这里只决定由哪个 Provider 执行业务。'
  if (currentTask.value?.provider_type === 'embedding')
    return '留空时自动使用第一个嵌入 Provider；这里只决定由哪个 Provider 执行业务。'
  return '未单独分配时沿用默认对话模型；这里只决定由哪个 Provider 执行该任务。'
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
    ElMessage.success(value ? `已分配给「${value}」` : '已改为跟随默认模型')
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

          <el-table v-if="loaded && (visibleProviders.length || providerQuery)" :data="visibleProviders" row-key="name">
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
        </section>
      </div>

      <div v-else key="routing" class="routing-layout">
        <aside class="task-side">
          <div class="vendor-head">
            <span class="vendor-title">功能任务</span>
            <el-button link type="primary" :icon="Refresh" :loading="routingLoading" @click="loadRouting">
              刷新
            </el-button>
          </div>
          <button
            v-for="task in routing?.tasks || []"
            :key="task.task"
            type="button"
            class="task-item"
            :class="{ active: selectedTask === task.task }"
            @click="selectedTask = task.task"
          >
            <span class="task-name">{{ task.label }}</span>
            <span class="task-meta">
              {{
                task.provider ||
                (isAutoCapability(task.provider_type)
                  ? '自动选择'
                  : `跟随默认 · ${routing?.default_provider || '未设置'}`)
              }}
            </span>
          </button>
          <div v-if="!routing?.tasks.length" class="task-empty">暂无任务</div>
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
                    ? '单独分配'
                    : isAutoCapability(currentTask.provider_type)
                      ? '自动选择'
                      : '跟随默认'
                }}
              </el-tag>
            </div>

            <div class="form-block">
              <div class="form-label">分配模型</div>
              <el-select
                :model-value="currentTask.provider"
                class="provider-select"
                :loading="routingSaving"
                @change="(value: string) => updateTaskProvider(value)"
              >
                <el-option value="" :label="taskFallbackLabel" />
                <el-option
                  v-for="provider in taskProviders"
                  :key="provider.name"
                  :value="provider.name"
                  :label="`${provider.name} · ${provider.model || provider.type_label}`"
                />
              </el-select>
              <div class="form-hint">{{ taskHint }}</div>
            </div>

            <div class="detail-grid">
              <div class="detail-item">
                <span class="detail-label">实际模型</span>
                <span class="detail-value">{{ assignedProvider?.model || '—' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">接口类型</span>
                <span class="detail-value">{{ assignedProvider?.type_label || '—' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">超时</span>
                <span class="detail-value">
                  {{ assignedProvider ? `${assignedProvider.timeout} 秒` : '—' }}
                </span>
              </div>
              <div class="detail-item">
                <span class="detail-label">密钥</span>
                <span class="detail-value">
                  {{ assignedProvider ? (assignedProvider.has_api_key ? '已配置' : '未配置') : '—' }}
                </span>
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
