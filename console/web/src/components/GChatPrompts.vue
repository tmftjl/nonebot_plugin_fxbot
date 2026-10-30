<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Edit, Plus } from '@element-plus/icons-vue'
import { chatApi } from '@/api'
import type { ChatTenant } from '@/types/api'

const props = defineProps<{ modelValue: unknown }>()
const emit = defineEmits<{ 'update:modelValue': [string[]] }>()

interface Rule {
  platform: string
  scene: string
  target: string
  prompt: string
}

interface Item {
  index: number
  raw: string
  rule: Rule | null
}

const SCENE_LABELS: Record<string, string> = { group: '群聊', private: '私聊' }

const tenants = ref<ChatTenant[]>([])
const visible = ref(false)
const editingIndex = ref<number | null>(null)
const form = ref<Rule>({ platform: '', scene: '', target: '', prompt: '' })

const tenantParts = (tenant: string): string[] => {
  const parts = tenant.split(':')
  return parts.length >= 4 ? [parts[0], parts[1], parts[2], parts.slice(3).join(':')] : ['', '', '', '']
}

const unique = (values: string[]) => [...new Set(values.filter(Boolean))]

function parse(entry: string): Rule | null {
  const [platform, target, scene, ...rest] = entry.split(':')
  const prompt = rest.join(':').trim()
  if (!platform?.trim() || !target?.trim() || !prompt) return null
  if (scene !== 'group' && scene !== 'private') return null
  return { platform: platform.trim(), target: target.trim(), scene, prompt }
}

const items = computed<Item[]>(() => {
  const list = Array.isArray(props.modelValue) ? props.modelValue : []
  return list.map((entry, index) => {
    const raw = String(entry)
    return { index, raw, rule: parse(raw) }
  })
})

const platformOptions = computed(() =>
  unique([...tenants.value.map((item) => tenantParts(item.tenant)[0]), form.value.platform]),
)

const targetOptions = computed(() => {
  const seen = new Set<string>()
  const options: { value: string; label: string; platform: string }[] = []
  for (const item of tenants.value) {
    const [platform, , scene, target] = tenantParts(item.tenant)
    if (form.value.platform && platform !== form.value.platform) continue
    if (form.value.scene && scene !== form.value.scene) continue
    if (!target || seen.has(target)) continue
    seen.add(target)
    options.push({ value: target, label: form.value.platform ? target : `${target} · ${platform}`, platform })
  }
  if (form.value.target && !seen.has(form.value.target)) {
    options.unshift({ value: form.value.target, label: form.value.target, platform: '' })
  }
  return options
})

const pickTarget = (value: string) => {
  form.value.target = value || ''
  if (!value || form.value.platform) return
  // 还没选平台时挑了某平台下的会话，顺手补上平台
  form.value.platform = targetOptions.value.find((item) => item.value === value)?.platform || ''
}

const titleOf = (item: Item) => {
  if (!item.rule) return item.raw
  return `${SCENE_LABELS[item.rule.scene]} ${item.rule.target}`
}

const summaryOf = (item: Item) => (item.rule ? `${item.rule.platform} · ${item.rule.prompt}` : '格式无法识别，编辑后保存会修正')

const openAdd = () => {
  editingIndex.value = null
  form.value = { platform: '', scene: '', target: '', prompt: '' }
  visible.value = true
}

const openEdit = (index: number) => {
  const item = items.value.find((row) => row.index === index)
  editingIndex.value = index
  form.value = item?.rule ? { ...item.rule } : { platform: '', scene: '', target: '', prompt: '' }
  visible.value = true
}

const remove = (index: number) => {
  const next = items.value.filter((row) => row.index !== index).map((row) => row.raw)
  emit('update:modelValue', next)
}

const save = () => {
  const rule: Rule = {
    platform: form.value.platform.trim(),
    scene: form.value.scene,
    target: form.value.target.trim(),
    prompt: form.value.prompt.trim(),
  }
  if (!rule.platform || !rule.scene || !rule.target || !rule.prompt) {
    ElMessage.warning('平台、聊天类型、会话 ID 和提示词都要填')
    return
  }
  const value = `${rule.platform}:${rule.target}:${rule.scene}:${rule.prompt}`
  const next = items.value.map((row) => row.raw)
  if (editingIndex.value === null) {
    if (next.includes(value)) {
      ElMessage.warning('已有相同的配置')
      return
    }
    next.push(value)
  } else {
    next[editingIndex.value] = value
  }
  emit('update:modelValue', next)
  visible.value = false
}

onMounted(async () => {
  try {
    tenants.value = (await chatApi.tenants()).tenants || []
  } catch {
    tenants.value = []
  }
})
</script>

<template>
  <div class="g-chat-prompts">
    <div class="items-list">
      <div v-for="item in items" :key="item.index" class="list-row">
        <span class="list-text">
          <span class="list-title" :title="titleOf(item)">{{ titleOf(item) }}</span>
          <span class="list-summary" :title="summaryOf(item)">{{ summaryOf(item) }}</span>
        </span>
        <span class="list-actions">
          <el-button link type="primary" :icon="Edit" @click="openEdit(item.index)" />
          <el-button link type="danger" :icon="Delete" @click="remove(item.index)" />
        </span>
      </div>
      <span v-if="!items.length" class="list-empty">还没有配置项</span>
      <el-button class="add-button" :icon="Plus" @click="openAdd">添加聊天要求</el-button>
    </div>

    <el-dialog v-model="visible" width="400px" align-center destroy-on-close append-to-body class="g-chat-prompts-dialog">
      <el-form label-width="88px">
        <el-form-item label="平台" required>
          <el-select
            v-model="form.platform"
            placeholder="选择或输入平台"
            filterable
            allow-create
            default-first-option
            clearable
            style="width: 100%"
          >
            <el-option v-for="item in platformOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="聊天类型" required>
          <el-select
            v-model="form.scene"
            placeholder="选择聊天类型"
            style="width: 100%"
            @update:model-value="form.target = ''"
          >
            <el-option label="群聊" value="group" />
            <el-option label="私聊" value="private" />
          </el-select>
        </el-form-item>
        <el-form-item label="会话 ID" required>
          <el-select
            :model-value="form.target"
            placeholder="选择已有会话或直接输入 ID"
            filterable
            allow-create
            default-first-option
            clearable
            style="width: 100%"
            @update:model-value="pickTarget"
          >
            <el-option v-for="item in targetOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <div v-if="!tenants.length" class="help-text">还没有可选会话，可以直接输入平台名与会话 ID。</div>
        </el-form-item>
        <el-form-item label="提示词" required>
          <el-input
            v-model="form.prompt"
            type="textarea"
            :rows="4"
            placeholder="只对这个聊天生效的额外要求"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.g-chat-prompts {
  width: 100%;

  .items-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: stretch;
  }

  .list-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 6px 8px 6px 12px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
  }

  .list-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .list-title {
    overflow: hidden;
    color: var(--color-text);
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .list-summary {
    overflow: hidden;
    color: var(--color-text-tertiary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .list-empty {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .list-actions {
    display: flex;
    flex: none;
    gap: 2px;

    :deep(.el-button + .el-button) {
      margin-left: 0;
    }
  }

  .add-button {
    align-self: flex-start;
  }

  .help-text {
    margin-top: 4px;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}
</style>

<style lang="scss">
/* 对话框传送到 body，作用域样式够不到，只能按类名限定 */
.g-chat-prompts-dialog {
  --el-dialog-padding-primary: 0;

  .el-dialog__header {
    display: none;
  }

  .el-dialog__body {
    padding: 30px 30px 0;
  }

  .el-dialog__footer {
    padding: 8px 30px 25px;
  }

  .el-form-item {
    margin-bottom: 14px;
  }

  .el-form-item__label {
    justify-content: flex-start;
    pointer-events: none;
  }
}
</style>
