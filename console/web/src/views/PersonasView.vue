<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { metaApi } from '@/api'

interface PersonaItem {
  key: string
  desc: string
  knowledgeCount?: number
}

const loading = ref(false)
const saving = ref(false)
const personas = ref<PersonaItem[]>([])
const dialogVisible = ref(false)
const editingKey = ref<string | null>(null)
const knowledgeSupported = ref(true)
const currentPersona = ref('')
const knowledgeDialogVisible = ref(false)
const knowledgeStats = ref({ count: 0 })
const loadingKnowledge = ref(false)
const importText = ref('')

const form = ref({ key: '', desc: '' })

const initial = (key: string): string => (key ? key.slice(0, 1).toUpperCase() : '?')

const loadPersonas = async () => {
  loading.value = true
  try {
    const res = await metaApi.getPersonas()
    personas.value = Object.entries(res || {}).map(([key, desc]) => ({
      key,
      desc: String(desc || ''),
      knowledgeCount: 0,
    }))
    if (knowledgeSupported.value) {
      for (const item of personas.value) {
        try {
          const stats = await metaApi.getKnowledgeStats(item.key)
          item.knowledgeCount = stats.count
        } catch (e: any) {
          const status = e?.response?.status
          if (status === 404 || status === 501) {
            knowledgeSupported.value = false
            break
          }
        }
      }
    }
  } catch (e: any) {
    ElMessage.error('加载人格失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

const openCreateDialog = () => {
  editingKey.value = null
  form.value = { key: '', desc: '' }
  dialogVisible.value = true
}

const openEditDialog = (item: PersonaItem) => {
  editingKey.value = item.key
  form.value = { key: item.key, desc: item.desc }
  dialogVisible.value = true
}

const savePersona = async () => {
  if (!form.value.key.trim()) {
    ElMessage.warning('请输入人格名称')
    return
  }
  if (!form.value.desc.trim()) {
    ElMessage.warning('请输入人格描述')
    return
  }
  saving.value = true
  try {
    const res = editingKey.value
      ? await metaApi.updatePersona(editingKey.value, form.value.desc)
      : await metaApi.createPersona(form.value.key, form.value.desc)
    if (!res.success) {
      ElMessage.error(res.message || '保存失败')
      return
    }
    dialogVisible.value = false
    await loadPersonas()
  } catch (e) {
    console.error(e)
    ElMessage.error('保存失败，请稍后再试')
  } finally {
    saving.value = false
  }
}

const deletePersona = async (key: string) => {
  try {
    await ElMessageBox.confirm(`确认删除人格 "${key}"?`, '删除人格', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const res = await metaApi.deletePersona(key)
    if (!res.success) {
      ElMessage.error('删除失败，默认人格不可删除')
      return
    }
    ElMessage.success(`已删除「${key}」`)
    await loadPersonas()
  } catch (e) {
    console.error(e)
    ElMessage.error('删除失败，请稍后再试')
  }
}

const openKnowledgeDialog = async (personaKey: string) => {
  if (!knowledgeSupported.value) {
    ElMessage.warning('当前版本未启用知识库功能')
    return
  }
  currentPersona.value = personaKey
  importText.value = ''
  knowledgeDialogVisible.value = true
  loadingKnowledge.value = true
  try {
    knowledgeStats.value = await metaApi.getKnowledgeStats(personaKey)
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 404 || status === 501) {
      knowledgeSupported.value = false
      knowledgeDialogVisible.value = false
      ElMessage.warning('当前版本未启用知识库功能')
      return
    }
    ElMessage.error('加载统计失败: ' + (e?.message || e))
  } finally {
    loadingKnowledge.value = false
  }
}

const importKnowledge = async () => {
  if (!importText.value.trim()) {
    ElMessage.warning('请输入要导入的文本')
    return
  }
  loadingKnowledge.value = true
  try {
    const res = await metaApi.importKnowledgeText(currentPersona.value, importText.value)
    ElMessage.success(res.message || '导入成功')
    knowledgeStats.value = await metaApi.getKnowledgeStats(currentPersona.value)
    knowledgeDialogVisible.value = false
    await loadPersonas()
  } catch (e: any) {
    ElMessage.error('导入失败: ' + (e?.message || e))
  } finally {
    loadingKnowledge.value = false
  }
}

const clearKnowledge = async () => {
  try {
    await ElMessageBox.confirm(`确认清空 "${currentPersona.value}" 的知识库?`, '清空知识库', { type: 'warning' })
    loadingKnowledge.value = true
    const res = await metaApi.clearKnowledge(currentPersona.value)
    ElMessage.success(res.message || '已清空')
    knowledgeStats.value = await metaApi.getKnowledgeStats(currentPersona.value)
    await loadPersonas()
  } catch {}
  finally {
    loadingKnowledge.value = false
  }
}

onMounted(() => {
  loadPersonas()
})
</script>

<template>
  <div v-loading="loading" class="personas-view">
    <div class="page-head">
      <div class="page-heading">
        <h2 class="page-title">人格管理</h2>
        <p class="page-subtitle">
          共 {{ personas.length }} 个人格 · 群聊可用 <code>/人格 名称</code> 覆盖，私聊按用户生效
        </p>
      </div>
      <div class="toolbar-right">
        <button class="btn btn-secondary" @click="loadPersonas">刷新</button>
        <button class="btn btn-primary" @click="openCreateDialog">+ 新增人格</button>
      </div>
    </div>

    <div v-if="!loading && personas.length === 0" class="empty-state">
      <div class="empty-icon">🎭</div>
      <div class="empty-title">还没有人格</div>
      <p class="empty-desc">人格就是发给模型的 system 提示词，先创建一个吧。</p>
      <button class="btn btn-primary" @click="openCreateDialog">创建第一个人格</button>
    </div>

    <div v-else class="persona-grid">
      <article v-for="item in personas" :key="item.key" class="persona-card">
        <div class="persona-top">
          <div class="persona-avatar" :class="{ primary: item.key === 'default' }">
            {{ initial(item.key) }}
          </div>
          <div class="persona-heading">
            <div class="persona-name-row">
              <span class="persona-name" :title="item.key">{{ item.key }}</span>
              <span v-if="item.key === 'default'" class="badge">默认</span>
            </div>
            <span v-if="knowledgeSupported" class="persona-meta">
              知识库 {{ item.knowledgeCount || 0 }} 条
            </span>
            <span v-else class="persona-meta muted">知识库未启用</span>
          </div>
        </div>

        <p class="persona-desc" :title="item.desc">{{ item.desc || '（空人格）' }}</p>

        <div class="persona-actions">
          <button v-if="knowledgeSupported" class="link-btn" @click="openKnowledgeDialog(item.key)">知识库</button>
          <button class="link-btn" @click="openEditDialog(item)">编辑</button>
          <button v-if="item.key !== 'default'" class="link-btn danger" @click="deletePersona(item.key)">删除</button>
        </div>
      </article>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingKey ? '编辑人格' : '新建人格'"
      width="620"
      align-center
    >
      <el-form :model="form" label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.key" :disabled="!!editingKey" placeholder="例如：猫娘" />
          <div class="form-hint">
            {{ editingKey ? '名称创建后不可修改。' : '名称同时作为文件名，不能包含 / \\ : * ? " < > |' }}
          </div>
        </el-form-item>
        <el-form-item label="人格描述">
          <el-input
            v-model="form.desc"
            type="textarea"
            :rows="9"
            placeholder="描述它的身份、语气、说话习惯和边界..."
          />
          <div class="form-hint">{{ form.desc.length }} 字 · 这段内容会作为 system 消息发给模型</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePersona">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-if="knowledgeSupported"
      v-model="knowledgeDialogVisible"
      :title="`知识库管理 · ${currentPersona}`"
      width="680"
      align-center
    >
      <div v-loading="loadingKnowledge">
        <div class="knowledge-stats">
          <span class="knowledge-stats-value">{{ knowledgeStats.count }}</span>
          <span class="knowledge-stats-label">条知识文档</span>
        </div>
        <el-input v-model="importText" type="textarea" :rows="9" placeholder="输入要导入的知识文本..." />
        <div class="knowledge-actions">
          <el-button @click="knowledgeDialogVisible = false">关闭</el-button>
          <el-button type="primary" @click="importKnowledge">导入文本</el-button>
          <el-button type="danger" plain @click="clearKnowledge">清空知识库</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.personas-view {
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
  font-size: 13px;
  color: var(--color-text-secondary);

  code {
    padding: 2px 6px;
    border-radius: var(--radius-sm);
    background: var(--color-bg-tertiary);
    color: var(--color-primary);
    font-family: inherit;
  }
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

  &:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
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

.persona-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.persona-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
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

.persona-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.persona-avatar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--color-info), var(--color-primary));
  box-shadow: var(--shadow-sm);
  color: #fff;
  font-size: 18px;
  font-weight: 800;

  &.primary {
    background: linear-gradient(135deg, var(--color-primary), #8b5cf6);
  }
}

.persona-heading {
  min-width: 0;
  flex: 1;
}

.persona-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.persona-name {
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

.persona-meta {
  display: block;
  margin-top: 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;

  &.muted {
    opacity: 0.7;
  }
}

.persona-desc {
  flex: 1;
  margin: 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}

.persona-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
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

.knowledge-stats {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 16px;
  padding: 16px 20px;
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(6, 182, 212, 0.08));
}

.knowledge-stats-value {
  color: var(--color-primary);
  font-size: 26px;
  font-weight: 800;
}

.knowledge-stats-label {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.knowledge-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}

@media (max-width: 640px) {
  .personas-view {
    padding: 16px;
  }

  .persona-grid {
    grid-template-columns: 1fr;
  }

  .toolbar-right {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
