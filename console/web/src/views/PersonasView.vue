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
    if (editingKey.value) {
      await metaApi.updatePersona(editingKey.value, form.value.desc)
    } else {
      await metaApi.createPersona(form.value.key, form.value.desc)
    }
    dialogVisible.value = false
    await loadPersonas()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.message || e))
  } finally {
    saving.value = false
  }
}

const deletePersona = async (key: string) => {
  try {
    await ElMessageBox.confirm(`确认删除人格 "${key}"?`, '删除', { type: 'warning' })
    await metaApi.deletePersona(key)
    await loadPersonas()
  } catch {}
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
    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">人格列表</h3>
        <div class="toolbar-right">
          <button class="btn btn-secondary btn-sm" @click="loadPersonas">刷新</button>
          <button class="btn btn-primary btn-sm" @click="openCreateDialog">新增人格</button>
        </div>
      </div>
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>名称</th>
              <th v-if="knowledgeSupported">知识库</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="personas.length === 0">
              <td :colspan="knowledgeSupported ? 3 : 2" class="text-center">暂无人格配置</td>
            </tr>
            <tr v-for="item in personas" :key="item.key">
              <td>
                <div class="persona-name">{{ item.key }}</div>
                <div class="persona-desc-preview">{{ item.desc }}</div>
              </td>
              <td v-if="knowledgeSupported">{{ item.knowledgeCount || 0 }} 条</td>
              <td>
                <button v-if="knowledgeSupported" class="btn-action btn-info" @click="openKnowledgeDialog(item.key)">知识库</button>
                <button class="btn-action btn-primary" @click="openEditDialog(item)">编辑</button>
                <button class="btn-action btn-danger" @click="deletePersona(item.key)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingKey ? '编辑人格' : '创建人格'" width="600">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.key" :disabled="!!editingKey" placeholder="人格标识名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.desc" type="textarea" :rows="8" placeholder="人格描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePersona">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-if="knowledgeSupported" v-model="knowledgeDialogVisible" :title="`知识库管理 - ${currentPersona}`" width="650">
      <div v-loading="loadingKnowledge">
        <div class="stats-banner">当前知识库共有 <strong>{{ knowledgeStats.count }}</strong> 条文档</div>
        <el-input v-model="importText" type="textarea" :rows="8" placeholder="输入要导入的知识文本..." />
        <div style="display:flex; gap: 8px; justify-content: flex-end; margin-top: 12px;">
          <el-button @click="knowledgeDialogVisible = false">关闭</el-button>
          <el-button type="primary" @click="importKnowledge">导入文本</el-button>
          <el-button type="danger" plain @click="clearKnowledge">清空知识库</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>
