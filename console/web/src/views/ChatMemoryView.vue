<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Delete } from '@element-plus/icons-vue'
import { chatApi } from '@/api'
import type { ChatTenant, ChatStyle, ChatJargon } from '@/types/api'

const tenants = ref<ChatTenant[]>([])
const tenant = ref('')
const style = ref<ChatStyle | null>(null)
const jargon = ref<ChatJargon[]>([])
const jargonStatus = ref('all')
const loading = ref(false)
const jargonLoading = ref(false)
const acting = ref(false)
const loaded = ref(false)

const formatTime = (value: string | null | undefined) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
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
</style>
