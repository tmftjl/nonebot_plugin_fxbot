<script setup lang="ts">
import { computed, ref } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { SchemaItem, ConfigCard } from '@/types/schema'
import GSubForm from './GSubForm.vue'

interface Props {
  card: ConfigCard
  modelValue: Record<string, any>
}

interface Emits {
  (e: 'update:modelValue', value: Record<string, any>): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const schemaGroups = computed(() => {
  const groups: { title?: string; schemas: SchemaItem[] }[] = []
  let currentGroup: { title?: string; schemas: SchemaItem[] } = { schemas: [] }

  for (const schema of props.card.schemas) {
    if (schema.component === 'Divider') {
      if (currentGroup.schemas.length > 0) {
        groups.push(currentGroup)
      }
      currentGroup = { title: schema.label, schemas: [] }
    } else {
      currentGroup.schemas.push(schema)
    }
  }

  if (currentGroup.schemas.length > 0) {
    groups.push(currentGroup)
  }

  return groups
})

const getValue = (field?: string) => {
  if (!field) return undefined
  const parts = field.split('.')
  let current: any = props.modelValue
  for (const part of parts) {
    if (current === undefined || current === null) return undefined
    current = current[part]
  }
  return current
}

const setValue = (field: string | undefined, value: any) => {
  if (!field) return
  const parts = field.split('.')
  const newData = JSON.parse(JSON.stringify(props.modelValue || {}))
  let current = newData

  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i]
    if (!current[part] || typeof current[part] !== 'object') {
      current[part] = {}
    }
    current = current[part]
  }

  current[parts[parts.length - 1]] = value
  emit('update:modelValue', newData)
}

const getColProps = (schema: SchemaItem) => {
  const type = schema.component
  // 全宽组件 (仅 GSubForm)
  if (type === 'GSubForm') {
    return { xs: 24, sm: 24, md: 24, lg: 24, xl: 24 }
  }
  
  // 其他所有组件默认均为 1/4 宽度 (LG/XL 下 span 6)
  // 包括 GTags, RadioGroup, CheckboxGroup, Input, Select, Switch, InputNumber
  return { xs: 24, sm: 12, md: 8, lg: 6, xl: 6 }
}

const getTooltipContent = (schema: SchemaItem) => {
    // 优先显示 helpMessage，其次 bottomHelpMessage，都为空则不显示
    return schema.helpMessage || schema.bottomHelpMessage || ''
}

// 数组输入相关逻辑
const arrayInputs = ref<Record<string, string>>({})
const arrayInputEls = ref<Record<string, any>>({})

const setArrayInputEl = (field: string, el: any) => {
  arrayInputEls.value[field] = el
}

const focusArrayInput = (field?: string) => {
  if (!field) return
  arrayInputEls.value[field]?.focus?.()
}

const normalizeStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  return value.map((v) => String(v))
}

const handleArrayInputConfirm = (field: string | undefined, currentTags: unknown) => {
  if (!field) return
  const tags = normalizeStringArray(currentTags)
  const val = (arrayInputs.value[field] || '').trim()
  if (!val) return
  if (!tags.includes(val)) setValue(field, [...tags, val])
  arrayInputs.value[field] = ''
}

const removeArrayItem = (field: string | undefined, currentTags: unknown, index: number) => {
  if (!field) return
  const tags = normalizeStringArray(currentTags)
  if (index < 0 || index >= tags.length) return
  const next = tags.slice()
  next.splice(index, 1)
  setValue(field, next)
}

const handleArrayInputBackspace = (field: string | undefined, currentTags: unknown) => {
  if (!field) return
  const inputVal = arrayInputs.value[field] || ''
  const tags = normalizeStringArray(currentTags)
  if (!inputVal && tags.length > 0) removeArrayItem(field, tags, tags.length - 1)
}
</script>

<template>
  <el-card class="config-card" shadow="never">
    <template #header>
      <div class="card-header">
        <div class="card-header-left">
          <span class="card-title">{{ card.title }}</span>
          <span v-if="card.desc" class="card-desc">{{ card.desc }}</span>
        </div>
        <div class="card-header-right">
          <slot name="header-extra"></slot>
        </div>
      </div>
    </template>

    <div class="card-content">
      <template v-for="(group, gIdx) in schemaGroups" :key="gIdx">
        <div v-if="group.title" class="group-title">
          <el-divider content-position="left">{{ group.title }}</el-divider>
        </div>

        <el-form label-width="120px" label-position="top" class="config-form">
          <el-row :gutter="32">
            <template v-for="schema in group.schemas" :key="schema.field">
              <el-col v-bind="getColProps(schema)">
                <el-form-item
                  :required="schema.required"
                >
                  <!-- 自定义 Label 区域，包含 Tooltip -->
                  <template #label>
                    <div class="custom-label">
                        <span>{{ schema.label }}</span>
                        <!-- 只有当有描述文字时才显示问号 -->
                        <el-tooltip
                            v-if="getTooltipContent(schema)"
                            :content="getTooltipContent(schema)"
                            placement="top"
                        >
                            <el-icon class="help-icon"><QuestionFilled /></el-icon>
                        </el-tooltip>
                    </div>
                  </template>

                  <!-- Switch -->
                  <div v-if="schema.component === 'Switch'" class="control-wrapper">
                    <el-switch
                      :model-value="getValue(schema.field)"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- Input -->
                  <div v-else-if="schema.component === 'Input'" class="control-wrapper narrow-input">
                    <el-input
                      :model-value="getValue(schema.field)"
                      :placeholder="schema.componentProps?.placeholder"
                      clearable
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- InputPassword -->
                  <div v-else-if="schema.component === 'InputPassword'" class="control-wrapper narrow-input">
                     <el-input
                      :model-value="getValue(schema.field)"
                      :placeholder="schema.componentProps?.placeholder"
                      type="password"
                      show-password
                      clearable
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- InputNumber -->
                  <div v-else-if="schema.component === 'InputNumber'" class="control-wrapper input-number-left narrow-input">
                    <el-input-number
                      :model-value="getValue(schema.field)"
                      :min="schema.componentProps?.min"
                      :max="schema.componentProps?.max"
                      :step="schema.componentProps?.step"
                      controls-position="right"
                      style="width: 100%"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- Textarea -->
                  <div v-else-if="schema.component === 'Textarea'" class="control-wrapper narrow-input">
                    <el-input
                      :model-value="getValue(schema.field)"
                      :placeholder="schema.componentProps?.placeholder"
                      type="textarea"
                      :rows="schema.componentProps?.rows || 3"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- Select -->
                  <div v-else-if="schema.component === 'Select'" class="control-wrapper narrow-input">
                    <el-select
                      :model-value="getValue(schema.field)"
                      :placeholder="schema.componentProps?.placeholder || '请选择'"
                      :multiple="schema.componentProps?.multiple || false"
                      :filterable="schema.componentProps?.filterable || false"
                      :clearable="schema.componentProps?.allowClear !== false"
                      style="width: 100%"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    >
                      <el-option
                        v-for="opt in schema.componentProps?.options || []"
                        :key="opt.value ?? opt"
                        :label="opt.label ?? opt"
                        :value="opt.value ?? opt"
                      />
                    </el-select>
                  </div>

                  <!-- RadioGroup -->
                  <div v-else-if="schema.component === 'RadioGroup'" class="control-wrapper">
                    <el-radio-group
                      :model-value="getValue(schema.field)"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    >
                      <el-radio
                        v-for="opt in schema.componentProps?.options || []"
                        :key="opt.value"
                        :value="opt.value"
                        border
                      >
                        {{ opt.label }}
                      </el-radio>
                    </el-radio-group>
                  </div>

                  <!-- GTags -->
                  <div v-else-if="schema.component === 'GTags'" class="control-wrapper">
                    <el-select
                      :model-value="getValue(schema.field) || []"
                      multiple
                      filterable
                      allow-create
                      default-first-option
                      :placeholder="schema.componentProps?.placeholder || '输入后按回车添加'"
                      style="width: 100%"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- GSubForm -->
                  <div v-else-if="schema.component === 'GSubForm'" class="control-wrapper">
                    <GSubForm
                      :model-value="getValue(schema.field) || {}"
                      :schemas="schema.componentProps?.schemas || []"
                      :multiple="schema.componentProps?.multiple !== false"
                      :key-field="schema.componentProps?.keyField"
                      :modal-title="schema.componentProps?.modalProps?.title"
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>

                  <!-- GArrayInput -->
                  <div v-else-if="schema.component === 'GArrayInput' && schema.field" class="control-wrapper narrow-input">
                    <div class="g-array-input-wrapper" @click="focusArrayInput(schema.field)">
                        <div class="tags-container">
                            <el-tag
                                v-for="(tag, idx) in (getValue(schema.field) || [])"
                                :key="idx"
                                closable
                                size="small"
                                class="array-tag"
                                type="info"
                                @close="removeArrayItem(schema.field, getValue(schema.field), idx)"
                            >
                                {{ tag }}
                            </el-tag>
                            <input
                                :ref="(el) => setArrayInputEl(schema.field, el)"
                                v-model="arrayInputs[schema.field]"
                                class="input-inner"
                                :placeholder="(getValue(schema.field) || []).length ? '' : (schema.componentProps?.placeholder || '输入后回车')"
                                @keydown.enter.prevent="handleArrayInputConfirm(schema.field, getValue(schema.field))"
                                @keydown.backspace="handleArrayInputBackspace(schema.field, getValue(schema.field))"
                                @blur="handleArrayInputConfirm(schema.field, getValue(schema.field))"
                            >
                        </div>
                    </div>
                  </div>

                  <!-- Default -->
                  <div v-else class="control-wrapper narrow-input">
                    <el-input
                      :model-value="getValue(schema.field)"
                      :placeholder="schema.componentProps?.placeholder"
                      clearable
                      @update:model-value="(v) => setValue(schema.field, v)"
                    />
                  </div>
                </el-form-item>
              </el-col>
            </template>
          </el-row>
        </el-form>
      </template>
    </div>
  </el-card>
</template>

<style scoped lang="scss">
.config-card {
  margin-bottom: 24px;
  border: 1px solid var(--color-border-light) !important;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
  transition: none; // 移除所有过渡动画
  border-radius: 12px !important;

  // 移除 hover 效果
  &:hover {
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    transform: none;
    border-color: var(--color-border-light) !important;
  }

  :deep(.el-card__header) {
    padding: 16px 24px;
    border-bottom: 1px solid var(--color-border-light);
    background-color: var(--color-bg-secondary);
  }

  :deep(.el-card__body) {
    padding: 24px;
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .card-title {
    font-size: 16px;
    font-weight: 700;
    color: var(--color-text);
  }

  .card-desc {
    margin-left: 12px;
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .card-content {
    .group-title {
      margin: 24px 0 16px;
      
      &:first-child {
        margin-top: 0;
      }

      :deep(.el-divider__text) {
        font-weight: 600;
        color: var(--color-text-secondary);
        font-size: 13px;
      }
    }

    .config-form {
      :deep(.el-form-item) {
        margin-bottom: 20px;
        padding: 0;
        
        .el-form-item__label {
          padding-bottom: 6px !important;
          line-height: 1.4;
          font-weight: 600;
          font-size: 14px;
          color: var(--color-text);
        }
      }

      .custom-label {
          display: flex;
          align-items: center;
          gap: 6px;

          .help-icon {
              color: var(--color-text-tertiary);
              cursor: pointer;
              font-size: 14px;
              transition: color 0.2s;

              &:hover {
                  color: var(--color-primary); // 悬停变色
              }
          }
      }

      .control-wrapper {
        width: 100%;
        
        &.narrow-input {
           max-width: 320px;
        }

        // 统一输入框组件样式微调
        :deep(.el-input__wrapper), :deep(.el-textarea__inner) {
          box-shadow: 0 0 0 1px var(--color-border) inset;
          
          &:hover {
             box-shadow: 0 0 0 1px var(--color-primary-light) inset;
          }
          
          &.is-focus {
             box-shadow: 0 0 0 1px var(--color-primary) inset !important;
          }
        }
      }
      

      // Input Number 左对齐修正
      .input-number-left {
          :deep(.el-input__inner) {
              text-align: left !important;
          }
      }

      // 数组输入样式
      .g-array-input-wrapper {
        width: 100%;
        min-height: 32px;
        padding: 1px 11px;
        background-color: var(--el-input-bg-color, var(--el-fill-color-blank));
        border: 1px solid var(--el-border-color);
        border-radius: var(--el-border-radius-base);
        transition: var(--el-transition-border);
        box-sizing: border-box;
        display: inline-flex;
        align-items: center;
        cursor: text;

        &:hover {
            border-color: var(--el-border-color-hover);
        }

        &:focus-within {
            border-color: var(--el-color-primary);
            box-shadow: 0 0 0 1px var(--el-color-primary) inset;
        }

        .tags-container {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            width: 100%;
            align-items: center;
            padding: 3px 0;
        }

        .input-inner {
            flex-grow: 1;
            width: 50px;
            border: none;
            outline: none;
            padding: 0;
            margin: 0;
            background: transparent;
            color: var(--el-text-color-regular);
            font-size: var(--el-font-size-base);
            min-width: 60px;
            height: 24px;
            line-height: 24px;

            &::placeholder {
            color: var(--el-text-color-placeholder);
            }
        }
      }
    }
  }
}
</style>
