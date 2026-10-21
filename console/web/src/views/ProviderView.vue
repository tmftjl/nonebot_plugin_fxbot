<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { providerApi } from '@/api'
import type { ProviderItem, ProviderTypeOption } from '@/types/api'

interface ProviderForm {
  name: string
  provider_type: string
  type: string
  api_key: string
  base_url: string
  model: string
  timeout: number
  embedding_dim: number | null
}

const capabilityOptions = [
  { label: '对话', value: 'chat' },
  { label: '嵌入', value: 'embedding' },
]

const loading = ref(false)
const saving = ref(false)
const providers = ref<ProviderItem[]>([])
const typeOptions = ref<ProviderTypeOption[]>([])
const dialogVisible = ref(false)
const editingName = ref<string | null>(null)
const form = ref<ProviderForm>(emptyForm())

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
  }
}

const availableTypeOptions = computed(() =>
  typeOptions.value.filter((item) => item.provider_type === form.value.provider_type)
)

const currentTypeOption = computed(() =>
  availableTypeOptions.value.find((item) => item.type === form.value.type)
)

const baseUrlPlaceholder = computed(() => {
  if (form.value.type === 'vertex') {
    return 'https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}'
  }
  return currentTypeOption.value?.default_base_url || 'https://api.example.com/v1'
})

const capabilityLabel = (value: string) => value === 'embedding' ? '嵌入' : '对话'

const applyTypeDefaults = (option?: ProviderTypeOption) => {
  if (!option) return
  form.value.base_url = option.default_base_url || ''
  form.value.timeout = option.default_timeout || 60
  form.value.embedding_dim = form.value.provider_type === 'embedding'
    ? (option.default_embedding_dim ?? 1536)
    : null
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
  loading.value = true
  try {
    const res = await providerApi.list()
    if (!res.success || !res.data) {
      ElMessage.error(res.message || '加载 Provider 失败')
      return
    }
    providers.value = res.data.providers || []
    typeOptions.value = res.data.types || []
  } catch (e) {
    console.error(e)
    ElMessage.error('加载 Provider 失败，请稍后再试')
  } finally {
    loading.value = false
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
  if (form.value.provider_type === 'embedding'
    && (!form.value.embedding_dim || form.value.embedding_dim <= 0)) {
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
    ElMessage.success(`已将「${item.name}」设为默认`)
    await loadProviders()
  } catch (e) {
    console.error(e)
    ElMessage.error('设置失败，请稍后再试')
  }
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

onMounted(() => {
  loadProviders()
})
</script>

<template>
  <div v-loading="loading" class="provider-view">
    <div class="page-head">
      <div class="page-heading">
        <h2 class="page-title">Provider 管理</h2>
        <p class="page-subtitle">
          共 {{ providers.length }} 个 Provider
          <span v-if="providers.length"> · 默认对话：{{ providers.find((item) => item.is_default)?.name || '未设置' }}</span>
        </p>
      </div>
      <div class="toolbar-right">
        <button class="btn btn-secondary" @click="loadProviders">刷新</button>
        <button class="btn btn-primary" :disabled="!typeOptions.length" @click="openCreateDialog">+ 添加 Provider</button>
      </div>
    </div>

    <div v-if="!loading && providers.length === 0" class="empty-state">
      <div class="empty-icon">🔌</div>
      <div class="empty-title">还没有 Provider</div>
      <p class="empty-desc">
        {{ typeOptions.length ? '添加一个模型服务，AI 对话才能工作。' : '未检测到可用的 Provider SDK，请先安装依赖。' }}
      </p>
      <button v-if="typeOptions.length" class="btn btn-primary" @click="openCreateDialog">添加第一个 Provider</button>
    </div>

    <div v-else class="provider-grid">
      <article v-for="item in providers" :key="item.name" class="provider-card">
        <div class="provider-top">
          <div class="provider-avatar" :class="item.provider_type">
            {{ item.provider_type === 'embedding' ? 'E' : 'C' }}
          </div>
          <div class="provider-heading">
            <div class="provider-name-row">
              <span class="provider-name" :title="item.name">{{ item.name }}</span>
              <span v-if="item.is_default" class="badge">默认</span>
            </div>
            <div class="provider-tags">
              <span class="chip">{{ item.type_label }}</span>
              <span class="chip capability" :class="item.provider_type">
                {{ capabilityLabel(item.provider_type) }}
              </span>
            </div>
          </div>
        </div>

        <div class="provider-fields">
          <div class="field-row">
            <span class="field-label">模型</span>
            <span class="field-value" :title="item.model">{{ item.model || '—' }}</span>
          </div>
          <div class="field-row">
            <span class="field-label">地址</span>
            <span class="field-value mono" :title="item.base_url">{{ item.base_url || '—' }}</span>
          </div>
          <div class="field-row">
            <span class="field-label">密钥</span>
            <span class="field-value" :class="{ muted: !item.has_api_key }">
              {{ item.has_api_key ? '已配置' : '未配置' }}
            </span>
          </div>
          <div v-if="item.provider_type === 'embedding'" class="field-row">
            <span class="field-label">维度</span>
            <span class="field-value">{{ item.embedding_dim || '—' }}</span>
          </div>
        </div>

        <div class="provider-actions">
          <button
            v-if="item.provider_type === 'chat' && !item.is_default"
            class="link-btn"
            @click="setDefault(item)"
          >
            设为默认
          </button>
          <button class="link-btn" @click="openEditDialog(item)">编辑</button>
          <button class="link-btn danger" @click="deleteProvider(item)">删除</button>
        </div>
      </article>
    </div>

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
            <el-option
              v-for="opt in availableTypeOptions"
              :key="opt.type"
              :label="opt.label"
              :value="opt.type"
            />
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
            <el-input-number v-model="form.timeout" :min="1" :max="600" controls-position="right" style="width: 100%" />
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
  padding: 24px;

  * {
    box-sizing: border-box;
  }
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.page-heading {
  min-width: 0;
}

.page-title {
  margin: 0 0 6px;
}

.page-subtitle {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.toolbar-right {
  display: flex;
  gap: 10px;
}

.btn {
  padding: 9px 18px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);

  &:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
}

.btn-primary {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-light));
  color: #fff;
  box-shadow: var(--shadow-sm);
}

.btn-secondary {
  background: var(--color-bg-tertiary);
  border-color: var(--color-border);
  color: var(--color-text-secondary);

  &:hover {
    color: var(--color-primary);
    border-color: var(--color-primary);
    box-shadow: none;
  }
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.provider-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  overflow: hidden;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: var(--transition);

  &::before {
    content: '';
    position: absolute;
    inset: 0 0 auto;
    height: 3px;
    background: linear-gradient(90deg, var(--color-primary), var(--color-info));
    opacity: 0;
    transition: var(--transition);
  }

  &:hover {
    transform: translateY(-3px);
    border-color: var(--color-primary);
    box-shadow: var(--shadow-lg);

    &::before {
      opacity: 1;
    }
  }
}

.provider-top {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.provider-avatar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--color-primary), var(--color-info));
  box-shadow: var(--shadow-sm);
  color: #fff;
  font-size: 17px;
  font-weight: 800;

  &.embedding {
    background: linear-gradient(135deg, var(--color-info), #8b5cf6);
  }
}

.provider-heading {
  min-width: 0;
  flex: 1;
}

.provider-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.provider-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: 15px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  flex: none;
  padding: 2px 8px;
  border: 1px solid rgba(59, 130, 246, 0.25);
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.12);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 700;
}

.provider-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.chip {
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 11px;

  &.chat {
    border-color: rgba(59, 130, 246, 0.25);
    background: rgba(59, 130, 246, 0.1);
    color: var(--color-primary);
  }

  &.embedding {
    border-color: rgba(139, 92, 246, 0.25);
    background: rgba(139, 92, 246, 0.1);
    color: #8b5cf6;
  }
}

.provider-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-lg);
  background: var(--color-bg-secondary);
}

.field-row {
  display: flex;
  gap: 12px;
  min-width: 0;
  font-size: 12px;
  line-height: 1.6;
}

.field-label {
  flex: none;
  width: 34px;
  color: var(--color-text-tertiary);
}

.field-value {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;

  &.mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }

  &.muted {
    color: var(--color-danger);
  }
}

.provider-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-light);
}

.link-btn {
  padding: 5px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition-fast);

  &:hover {
    background: rgba(59, 130, 246, 0.1);
  }

  &.danger {
    color: var(--color-danger);

    &:hover {
      background: rgba(239, 68, 68, 0.1);
    }
  }
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

.form-hint {
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 640px) {
  .provider-view {
    padding: 16px;
  }

  .provider-grid {
    grid-template-columns: 1fr;
  }

  .toolbar-right {
    width: 100%;
    justify-content: flex-end;
  }

  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>
