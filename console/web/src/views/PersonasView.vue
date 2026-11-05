<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import ListPager from '@/components/ListPager.vue'
import { usePagination } from '@/composables/usePagination'
import { metaApi } from '@/api'

interface PersonaItem {
  key: string
  desc: string
  knowledgeCount?: number
}

interface PersonaForm {
  key: string
  intro: string
  personality: string
  behavior_style: string
  reply_style: string
}

const SECTION_KEYS: Record<string, 'personality' | 'behavior_style' | 'reply_style'> = {
  人格特征: 'personality',
  行为风格: 'behavior_style',
  回复风格: 'reply_style',
}

const emptyForm = (): PersonaForm => ({
  key: '',
  intro: '',
  personality: '',
  behavior_style: '',
  reply_style: '',
})

const parsePersona = (text: string): { form: Omit<PersonaForm, 'key'>; raw: boolean } => {
  const matches = [...text.matchAll(/^#{1,6}[ \t]+(.+?)[ \t]*$/gm)]
  if (!matches.length) {
    return { form: { ...emptyForm(), personality: text.trim() }, raw: false }
  }
  const form = { ...emptyForm(), intro: text.slice(0, matches[0].index ?? 0).trim() }
  const seen = new Set<string>()
  for (let index = 0; index < matches.length; index += 1) {
    const key = SECTION_KEYS[matches[index][1].trim()]
    if (!key || seen.has(key)) {
      // 自定义小节映射不到三段表单：按原文编辑、原样保存，避免被反复套壳
      return { form: { ...emptyForm(), personality: text.trim() }, raw: true }
    }
    seen.add(key)
    const end = matches[index + 1]?.index ?? text.length
    form[key] = text.slice((matches[index].index ?? 0) + matches[index][0].length, end).trim()
  }
  return { form, raw: false }
}

const buildPersonaText = (form: PersonaForm): string => {
  const parts: string[] = []
  if (form.intro.trim()) parts.push(form.intro.trim())
  if (form.personality.trim()) parts.push(`## 人格特征\n${form.personality.trim()}`)
  if (form.behavior_style.trim()) parts.push(`## 行为风格\n${form.behavior_style.trim()}`)
  if (form.reply_style.trim()) parts.push(`## 回复风格\n${form.reply_style.trim()}`)
  return parts.join('\n\n')
}

const personaPreview = (text: string): string =>
  text
    .replace(/^#{1,6}[ \t]+.*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

const loading = ref(false)
const saving = ref(false)
const personas = ref<PersonaItem[]>([])
const defaultPersona = ref('')
const activeSection = ref('personality')
const dialogVisible = ref(false)
const editingKey = ref<string | null>(null)
const rawMode = ref(false)
const knowledgeSupported = ref(true)
const currentPersona = ref('')
const knowledgeDialogVisible = ref(false)
const knowledgeStats = ref({ count: 0 })
const loadingKnowledge = ref(false)
const importText = ref('')

const form = ref<PersonaForm>(emptyForm())

const PERSONA_PAGE_SIZE = 12
const {
  page: personaPage,
  total: personaTotal,
  paged: pagedPersonas,
} = usePagination(personas, PERSONA_PAGE_SIZE)

const initial = (key: string): string => (key ? key.slice(0, 1).toUpperCase() : '?')

const loadPersonas = async () => {
  loading.value = true
  try {
    const res = await metaApi.getPersonas()
    defaultPersona.value = res?.default || ''
    personas.value = Object.entries(res?.items || {}).map(([key, desc]) => ({
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
  rawMode.value = false
  form.value = emptyForm()
  activeSection.value = 'personality'
  dialogVisible.value = true
}

const openEditDialog = (item: PersonaItem) => {
  const parsed = parsePersona(item.desc)
  editingKey.value = item.key
  rawMode.value = parsed.raw
  form.value = { key: item.key, ...parsed.form }
  activeSection.value = 'personality'
  dialogVisible.value = true
}

const savePersona = async () => {
  if (!form.value.key.trim()) {
    ElMessage.warning('请输入人格名称')
    return
  }
  if (rawMode.value && !form.value.personality.trim()) {
    ElMessage.warning('人格内容不能为空')
    return
  }
  if (!rawMode.value && !form.value.personality.trim() && !form.value.behavior_style.trim() && !form.value.reply_style.trim()) {
    activeSection.value = 'personality'
    ElMessage.warning('请至少填写一段人格内容')
    return
  }
  saving.value = true
  try {
    const desc = rawMode.value ? form.value.personality.trim() : buildPersonaText(form.value)
    const res = editingKey.value
      ? await metaApi.updatePersona(editingKey.value, desc)
      : await metaApi.createPersona(form.value.key, desc)
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
      ElMessage.error(res.message || '删除失败，请稍后再试')
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
  <div class="personas-view">
    <PageHeader
      title="人格管理"
      :subtitle="`共 ${personas.length} 个人格 · 群聊可用 /人格 名称 覆盖，私聊按用户生效`"
    >
      <template #actions>
        <button class="btn btn-secondary" @click="loadPersonas">刷新</button>
        <button class="btn btn-primary" @click="openCreateDialog">+ 新增人格</button>
      </template>
    </PageHeader>

    <div v-if="!loading && personas.length === 0" class="empty-state">
      <div class="empty-icon">🎭</div>
      <div class="empty-title">还没有人格</div>
      <p class="empty-desc">人格按 MaiBot 的人格特征、行为风格、回复风格三段填写，先创建一个吧。</p>
      <button class="btn btn-primary" @click="openCreateDialog">创建第一个人格</button>
    </div>

    <div v-else class="persona-grid">
      <article v-for="item in pagedPersonas" :key="item.key" class="persona-card">
        <div class="persona-top">
          <div class="persona-avatar" :class="{ primary: item.key === defaultPersona }">
            {{ initial(item.key) }}
          </div>
          <div class="persona-heading">
            <div class="persona-name-row">
              <span class="persona-name" :title="item.key">{{ item.key }}</span>
              <span v-if="item.key === defaultPersona" class="badge">默认</span>
            </div>
            <span v-if="knowledgeSupported" class="persona-meta">
              知识库 {{ item.knowledgeCount || 0 }} 条
            </span>
            <span v-else class="persona-meta muted">知识库未启用</span>
          </div>
        </div>

        <p class="persona-desc" :title="personaPreview(item.desc)">
          {{ personaPreview(item.desc) || '（空人格）' }}
        </p>

        <div class="persona-actions">
          <button v-if="knowledgeSupported" class="link-btn" @click="openKnowledgeDialog(item.key)">知识库</button>
          <button class="link-btn" @click="openEditDialog(item)">编辑</button>
          <button v-if="item.key !== defaultPersona" class="link-btn danger" @click="deletePersona(item.key)">删除</button>
        </div>
      </article>
    </div>
    <ListPager v-model:page="personaPage" :total="personaTotal" :page-size="PERSONA_PAGE_SIZE" />

    <el-dialog
      v-model="dialogVisible"
      :title="editingKey ? '编辑人格' : '新建人格'"
      width="680"
      align-center
    >
      <el-form :model="form" label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.key" :disabled="!!editingKey" placeholder="例如：猫娘" />
          <div class="form-hint">
            {{ editingKey ? '名称创建后不可修改。' : '名称同时作为文件名，不能包含 / \\ : * ? " < > |' }}
          </div>
        </el-form-item>
        <el-form-item v-if="!rawMode" label="身份介绍">
          <el-input
            v-model="form.intro"
            type="textarea"
            :rows="2"
            placeholder="可留空，例如：你的名字是雪球。"
          />
        </el-form-item>
      </el-form>
      <el-tabs v-if="!rawMode" v-model="activeSection" class="persona-tabs">
        <el-tab-pane label="人格特征" name="personality">
          <el-input
            v-model="form.personality"
            type="textarea"
            :rows="10"
            placeholder="她是谁、性格、背景和喜好..."
          />
          <div class="form-hint">{{ form.personality.length }} 字 · 对应 MaiBot 的 personality</div>
        </el-tab-pane>
        <el-tab-pane label="行为风格" name="behavior_style">
          <el-input
            v-model="form.behavior_style"
            type="textarea"
            :rows="10"
            placeholder="如何观察局面、何时参与、如何保持人设..."
          />
          <div class="form-hint">
            {{ form.behavior_style.length }} 字 · 只影响 Planner 的思考与决策，不改变回复正文
          </div>
        </el-tab-pane>
        <el-tab-pane label="回复风格" name="reply_style">
          <el-input
            v-model="form.reply_style"
            type="textarea"
            :rows="10"
            placeholder="句子长短、语气、口头习惯和禁忌..."
          />
          <div class="form-hint">{{ form.reply_style.length }} 字 · 对应 MaiBot 的 reply_style</div>
        </el-tab-pane>
      </el-tabs>
      <template v-else>
        <el-input
          v-model="form.personality"
          type="textarea"
          :rows="16"
          placeholder="按原文编辑人格文件..."
        />
        <div class="form-hint">该文件包含自定义小节，拆不成三段，保存时按原文写回。</div>
      </template>
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
      <div>
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
  padding: 24px 28px 32px;
}

.persona-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.persona-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  transition: border-color 0.15s ease;

  &:hover {
    border-color: var(--color-text-tertiary);
  }
}

.persona-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.persona-avatar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 16px;
  font-weight: 600;

  &.primary {
    border-color: var(--color-primary);
    background: var(--color-primary);
    color: #fff;
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
  border: 1px solid rgba(224, 111, 6, 0.25);
  border-radius: 999px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 600;
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
    background: var(--color-primary-soft);
  }

  &.danger {
    color: var(--color-danger);

    &:hover {
      background: rgba(217, 74, 74, 0.1);
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

.persona-tabs {
  margin-top: -4px;
}

.knowledge-stats {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 16px;
  padding: 14px 18px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-tertiary);
}

.knowledge-stats-value {
  color: var(--color-primary);
  font-size: 24px;
  font-weight: 700;
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

  .page-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
