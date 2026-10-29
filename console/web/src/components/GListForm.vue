<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Edit, Plus } from '@element-plus/icons-vue'
import type { SchemaItem } from '@/types/schema'
import GChatTenant from './GChatTenant.vue'
import GTimeRange from './GTimeRange.vue'

const props = withDefaults(
  defineProps<{
    modelValue: Record<string, any>[]
    schemas: SchemaItem[]
    titleField?: string
    addLabel?: string
  }>(),
  { titleField: '', addLabel: '添加' },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>[]] }>()

const SCENE_LABELS: Record<string, string> = { group: '群聊', private: '私聊' }

const visible = ref(false)
const editingIndex = ref<number | null>(null)
const form = ref<Record<string, any>>({})

const list = computed(() => (Array.isArray(props.modelValue) ? props.modelValue : []))

const emptyItem = () => {
  const data: Record<string, any> = {}
  for (const schema of props.schemas) {
    if (!schema.field) continue
    if (schema.componentProps?.defaultValue !== undefined) {
      data[schema.field] = schema.componentProps.defaultValue
    } else if (schema.component === 'GTags' || schema.component === 'GListForm') {
      data[schema.field] = []
    } else if (schema.component === 'Switch') {
      data[schema.field] = false
    } else if (schema.component === 'GChatTenant' || schema.component === 'GTimeRange') {
      data[schema.field] = ''
    }
  }
  return data
}

const openAdd = () => {
  editingIndex.value = null
  form.value = emptyItem()
  visible.value = true
}

const openEdit = (index: number) => {
  editingIndex.value = index
  form.value = JSON.parse(JSON.stringify(list.value[index] || {}))
  visible.value = true
}

const remove = (index: number) => {
  const next = list.value.slice()
  next.splice(index, 1)
  emit('update:modelValue', next)
}

const save = () => {
  for (const schema of props.schemas) {
    if (!schema.required || !schema.field) continue
    const value = form.value[schema.field]
    if (typeof value !== 'string' || !value.trim()) {
      ElMessage.warning(`${schema.label}不能为空`)
      return
    }
  }
  if (editingIndex.value === null) {
    emit('update:modelValue', [...list.value, { ...form.value }])
  } else {
    const next = list.value.slice()
    next[editingIndex.value] = { ...form.value }
    emit('update:modelValue', next)
  }
  visible.value = false
}

const display = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'boolean') return value ? '开' : '关'
  if (Array.isArray(value)) {
    if (!value.length) return ''
    return value.every((item) => typeof item !== 'object') ? value.join('、') : `${value.length} 项`
  }
  if (typeof value === 'object') return ''
  const text = String(value)
  const tenant = /^[^:]+:[^:]+:(group|private):(.+)$/.exec(text)
  if (!tenant) return text
  // 聊天流串太长，列表里只显示聊天类型与截短的会话 ID
  const target = tenant[2].length > 10 ? `${tenant[2].slice(0, 10)}…` : tenant[2]
  return `${SCENE_LABELS[tenant[1]] || tenant[1]} ${target}`
}

const titleOf = (item: Record<string, any>, index: number) => {
  return display(props.titleField ? item[props.titleField] : '') || `第 ${index + 1} 项`
}

const summaryOf = (item: Record<string, any>) => {
  const parts: string[] = []
  for (const schema of props.schemas) {
    if (!schema.field || schema.field === props.titleField) continue
    const text = display(item[schema.field])
    if (text) parts.push(`${schema.label} ${text}`)
  }
  return parts.join(' · ')
}

const getFormValue = (field: string) => form.value[field]

const setFormValue = (field: string | undefined, value: unknown) => {
  if (field) form.value[field] = value
}
</script>

<template>
  <div class="g-list-form">
    <div class="items-list">
      <div v-for="(item, index) in list" :key="index" class="list-row">
        <span class="list-text">
          <span class="list-title" :title="titleOf(item, index)">{{ titleOf(item, index) }}</span>
          <span v-if="summaryOf(item)" class="list-summary" :title="summaryOf(item)">{{ summaryOf(item) }}</span>
        </span>
        <span class="list-actions">
          <el-button link type="primary" :icon="Edit" @click="openEdit(index)" />
          <el-button link type="danger" :icon="Delete" @click="remove(index)" />
        </span>
      </div>
      <span v-if="!list.length" class="list-empty">还没有配置项</span>
      <el-button class="add-button" :icon="Plus" @click="openAdd">{{ addLabel }}</el-button>
    </div>

    <el-dialog v-model="visible" width="620px" destroy-on-close append-to-body class="g-list-dialog">
      <template #header>
        <span>{{ editingIndex === null ? addLabel : '编辑' }}</span>
      </template>
      <el-form label-width="110px">
        <template v-for="schema in schemas" :key="schema.field || schema.label">
          <el-form-item v-if="schema.field" :label="schema.label" :required="schema.required">
            <el-input
              v-if="schema.component === 'Input'"
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder"
              clearable
              @update:model-value="(v: string) => setFormValue(schema.field, v)"
            />
            <el-input
              v-else-if="schema.component === 'Textarea'"
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder"
              type="textarea"
              :rows="schema.componentProps?.rows || 3"
              @update:model-value="(v: string) => setFormValue(schema.field, v)"
            />
            <el-input-number
              v-else-if="schema.component === 'InputNumber'"
              class="number-input"
              :model-value="getFormValue(schema.field)"
              :min="schema.componentProps?.min"
              :max="schema.componentProps?.max"
              :step="schema.componentProps?.step"
              controls-position="right"
              @update:model-value="(v: number | undefined) => setFormValue(schema.field, v)"
            />
            <el-switch
              v-else-if="schema.component === 'Switch'"
              :model-value="getFormValue(schema.field)"
              @update:model-value="(v: boolean) => setFormValue(schema.field, v)"
            />
            <el-select
              v-else-if="schema.component === 'Select'"
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder || '请选择'"
              clearable
              style="width: 100%"
              @update:model-value="(v: unknown) => setFormValue(schema.field, v)"
            >
              <el-option
                v-for="opt in schema.componentProps?.options || []"
                :key="opt.value ?? opt"
                :label="opt.label ?? opt"
                :value="opt.value ?? opt"
              />
            </el-select>
            <el-select
              v-else-if="schema.component === 'GTags'"
              :model-value="getFormValue(schema.field) || []"
              multiple
              filterable
              allow-create
              default-first-option
              :placeholder="schema.componentProps?.placeholder || '输入后按回车添加'"
              style="width: 100%"
              @update:model-value="(v: unknown) => setFormValue(schema.field, v)"
            />
            <GChatTenant
              v-else-if="schema.component === 'GChatTenant'"
              :model-value="getFormValue(schema.field) || ''"
              @update:model-value="(v: string) => setFormValue(schema.field, v)"
            />
            <GTimeRange
              v-else-if="schema.component === 'GTimeRange'"
              :model-value="getFormValue(schema.field) || ''"
              :placeholder="schema.componentProps?.placeholder"
              @update:model-value="(v: string) => setFormValue(schema.field, v)"
            />
            <GListForm
              v-else-if="schema.component === 'GListForm'"
              :model-value="getFormValue(schema.field) || []"
              :schemas="schema.componentProps?.schemas || []"
              :title-field="schema.componentProps?.titleField || ''"
              :add-label="schema.componentProps?.addLabel || '添加'"
              @update:model-value="(v) => setFormValue(schema.field, v)"
            />
            <el-input
              v-else
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder"
              clearable
              @update:model-value="(v: string) => setFormValue(schema.field, v)"
            />
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>
        </template>
      </el-form>

      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.g-list-form {
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
.g-list-dialog {
  .el-dialog__body {
    padding: 8px 20px 4px;
  }

  .el-form-item {
    margin-bottom: 18px;
  }

  .number-input {
    width: 160px;
  }
}
</style>
