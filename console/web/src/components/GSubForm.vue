<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { SchemaItem } from '@/types/schema'

interface Props {
  modelValue: Record<string, any>
  schemas: SchemaItem[]
  multiple?: boolean
  keyField?: string
  modalTitle?: string
}

interface Emits {
  (e: 'update:modelValue', value: Record<string, any>): void
}

const props = withDefaults(defineProps<Props>(), {
  multiple: true,
  keyField: '_key',
  modalTitle: '编辑'
})
const emit = defineEmits<Emits>()

const dialogVisible = ref(false)
const editingKey = ref<string | null>(null)
const formData = ref<Record<string, any>>({})

const items = computed(() => {
  const data = props.modelValue || {}
  return Object.entries(data).map(([key, value]) => ({
    _key: key,
    ...(typeof value === 'object' ? value : { value })
  }))
})

const openAdd = () => {
  editingKey.value = null
  // 从 schema 中读取默认值
  const defaultValues: Record<string, any> = {}
  for (const schema of props.schemas) {
    if (schema.field && schema.componentProps?.defaultValue !== undefined) {
      defaultValues[schema.field] = schema.componentProps.defaultValue
    }
  }
  formData.value = defaultValues
  dialogVisible.value = true
}

const openEdit = (key: string) => {
  editingKey.value = key
  const item = props.modelValue?.[key]
  formData.value = { _key: key, ...(typeof item === 'object' ? item : {}) }
  dialogVisible.value = true
}

const handleDelete = (key: string) => {
  const newData = { ...props.modelValue }
  delete newData[key]
  emit('update:modelValue', newData)
}

const handleSave = () => {
  const key = formData.value._key || formData.value[props.keyField]
  if (!key) {
    ElMessage.warning('请输入标识名称')
    return
  }

  if (editingKey.value === null && props.modelValue?.[key]) {
    ElMessage.warning('该标识已存在')
    return
  }

  const newData = { ...props.modelValue }

  if (editingKey.value !== null && editingKey.value !== key) {
    delete newData[editingKey.value]
  }

  const { _key, ...rest } = formData.value
  newData[key] = rest
  emit('update:modelValue', newData)
  dialogVisible.value = false
}

const getFormValue = (field: string) => {
  return formData.value[field]
}

const setFormValue = (field: string, value: any) => {
  formData.value[field] = value
}
</script>

<template>
  <div class="g-sub-form">
    <div class="items-list">
      <el-tag
        v-for="item in items"
        :key="item._key"
        closable
        size="small"
        class="item-tag"
        @click="openEdit(item._key)"
        @close="handleDelete(item._key)"
      >
        {{ item._key }}
      </el-tag>
      <el-button type="primary" size="small" @click="openAdd">
        <el-icon><Plus /></el-icon>
        添加
      </el-button>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingKey ? `编辑 - ${editingKey}` : '添加'"
      width="500"
      destroy-on-close
      append-to-body
    >
      <el-form label-width="120px" label-position="left">
        <el-form-item label="标识名称" required>
          <el-input
            v-model="formData._key"
            placeholder="请输入唯一标识"
            :disabled="editingKey !== null"
          />
        </el-form-item>

        <template v-for="schema in schemas" :key="schema.field || schema.label">
          <el-form-item
            v-if="schema.component === 'Input' && schema.field"
            :label="schema.label"
            :required="schema.required"
          >
            <el-input
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder"
              clearable
              @update:model-value="(v) => setFormValue(schema.field, v)"
            />
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>

          <el-form-item
            v-else-if="schema.component === 'InputPassword' && schema.field"
            :label="schema.label"
            :required="schema.required"
          >
            <el-input
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder"
              type="password"
              show-password
              clearable
              @update:model-value="(v) => setFormValue(schema.field, v)"
            />
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>

          <el-form-item
            v-else-if="schema.component === 'InputNumber' && schema.field"
            :label="schema.label"
            :required="schema.required"
          >
            <el-input-number
              :model-value="getFormValue(schema.field)"
              :min="schema.componentProps?.min"
              :max="schema.componentProps?.max"
              :step="schema.componentProps?.step"
              @update:model-value="(v) => setFormValue(schema.field, v)"
            />
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>

          <el-form-item
            v-else-if="schema.component === 'Switch' && schema.field"
            :label="schema.label"
            :required="schema.required"
          >
            <el-switch
              :model-value="getFormValue(schema.field)"
              @update:model-value="(v) => setFormValue(schema.field, v)"
            />
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>

          <el-form-item
            v-else-if="schema.component === 'Select' && schema.field"
            :label="schema.label"
            :required="schema.required"
          >
            <el-select
              :model-value="getFormValue(schema.field)"
              :placeholder="schema.componentProps?.placeholder || '请选择'"
              clearable
              style="width: 100%"
              @update:model-value="(v) => setFormValue(schema.field, v)"
            >
              <el-option
                v-for="opt in schema.componentProps?.options || []"
                :key="opt.value ?? opt"
                :label="opt.label ?? opt"
                :value="opt.value ?? opt"
              />
            </el-select>
            <div v-if="schema.bottomHelpMessage" class="help-text">{{ schema.bottomHelpMessage }}</div>
          </el-form-item>
        </template>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.g-sub-form {
  width: 100%;

  .items-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }

  .item-tag {
    cursor: pointer;
    font-size: 12px;
    transition: all 0.2s ease;

    &:hover {
      opacity: 0.8;
      background-color: var(--el-color-primary-light-3);
    }

    &:active {
      background-color: var(--el-color-primary);
      color: white;
    }
  }

  .help-text {
    margin-top: 4px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}
</style>
