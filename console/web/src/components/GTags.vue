<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowDown, Close, Plus } from '@element-plus/icons-vue'
import { chatApi } from '@/api'

const props = withDefaults(
  defineProps<{
    modelValue: unknown
    placeholder?: string
    suggestScene?: string
  }>(),
  { placeholder: '输入后按回车添加', suggestScene: '' },
)

const emit = defineEmits<{ 'update:modelValue': [string[]] }>()

const visible = ref(false)
const keyword = ref('')
const inputEl = ref<{ focus: () => void }>()
const suggestions = ref<{ id: string; label: string }[]>([])
let suggestionsLoaded = false

const tags = computed(() => (Array.isArray(props.modelValue) ? props.modelValue.map(String) : []))
const longMode = computed(() => tags.value.some((tag) => tag.length > 16 || tag.includes('\n')))
const summary = computed(() => {
  if (!tags.value.length) return ''
  return longMode.value ? `已配置 ${tags.value.length} 条` : tags.value[0]
})
const badge = computed(() =>
  !longMode.value && tags.value.length > 1 ? `+${tags.value.length - 1}` : '',
)
const key = computed(() => keyword.value.trim())
const filtered = computed(() => (key.value ? tags.value.filter((tag) => tag.includes(key.value)) : tags.value))
const availableSuggestions = computed(() =>
  suggestions.value
    .filter((item) => !tags.value.includes(item.id))
    .filter((item) => !key.value || item.id.includes(key.value) || item.label.includes(key.value)),
)

const loadSuggestions = async () => {
  if (suggestionsLoaded || !props.suggestScene) return
  suggestionsLoaded = true
  try {
    const { tenants } = await chatApi.tenants()
    const seen = new Set<string>()
    const picked: { id: string; label: string }[] = []
    for (const item of tenants || []) {
      const parts = String(item.tenant).split(':')
      if (parts.length < 4 || parts[2] !== props.suggestScene) continue
      const id = parts.slice(3).join(':')
      if (!id || seen.has(id)) continue
      seen.add(id)
      picked.push({ id, label: `${id} · ${parts[0]}` })
    }
    suggestions.value = picked
  } catch {
    suggestions.value = []
  }
}

const setTags = (next: string[]) => emit('update:modelValue', next)

const add = () => {
  const value = keyword.value.trim()
  if (!value) return
  if (!tags.value.includes(value)) setTags([...tags.value, value])
  keyword.value = ''
}

const remove = (target: string) => setTags(tags.value.filter((tag) => tag !== target))

const pick = (id: string) => {
  if (!tags.value.includes(id)) setTags([...tags.value, id])
}

const onEnter = (event: KeyboardEvent) => {
  // 输入法选词的回车不应提交标签
  if (event.isComposing) return
  event.preventDefault()
  add()
}

watch(visible, (open) => {
  if (!open) return
  keyword.value = ''
  void loadSuggestions()
  nextTick(() => inputEl.value?.focus())
})
</script>

<template>
  <el-popover
    v-model:visible="visible"
    trigger="click"
    placement="bottom-start"
    :width="360"
  >
    <template #reference>
      <div class="tag-field" :class="{ 'is-empty': !tags.length, 'is-open': visible }">
        <span v-if="tags.length" class="tag-field__value" :title="longMode ? tags.join('\n') : summary">
          {{ summary }}
        </span>
        <span v-else class="tag-field__placeholder">{{ placeholder }}</span>
        <span v-if="badge" class="tag-field__badge">{{ badge }}</span>
        <el-icon class="tag-field__arrow"><ArrowDown /></el-icon>
      </div>
    </template>

    <div class="tag-editor">
      <el-input
        ref="inputEl"
        v-model="keyword"
        placeholder="搜索或输入新标签，回车添加"
        clearable
        @keydown.enter="onEnter"
        @keydown.esc="visible = false"
      />
      <div v-if="availableSuggestions.length" class="tag-editor__group">
        <div class="tag-editor__meta">已有会话</div>
        <div class="tag-editor__list is-suggest">
          <button
            v-for="item in availableSuggestions"
            :key="item.id"
            type="button"
            class="tag-row tag-row--suggest"
            @click="pick(item.id)"
          >
            <span class="tag-row__text">{{ item.label }}</span>
            <el-icon class="tag-row__add"><Plus /></el-icon>
          </button>
        </div>
      </div>
      <div class="tag-editor__meta">已添加 {{ tags.length }} 个标签</div>
      <div class="tag-editor__list">
        <div v-for="tag in filtered" :key="tag" class="tag-row">
          <span class="tag-row__text" :title="tag">{{ tag }}</span>
          <button class="tag-row__remove" type="button" :title="`删除 ${tag}`" @click.stop="remove(tag)">
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <div v-if="!filtered.length" class="tag-editor__empty">
          {{ keyword.trim() ? '按回车添加这条' : '还没有标签，输入后按回车添加' }}
        </div>
      </div>
    </div>
  </el-popover>
</template>

<style scoped lang="scss">
.tag-field {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  box-sizing: border-box;
  cursor: pointer;
  transition: border-color 0.15s ease;

  &:hover {
    border-color: var(--color-text-tertiary);
  }

  &.is-open {
    border-color: var(--color-primary);
  }

  .tag-field__value {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 18px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tag-field__placeholder {
    flex: 1;
    overflow: hidden;
    color: var(--color-text-tertiary);
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tag-field__badge {
    flex: none;
    padding: 1px 7px;
    border-radius: 9px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
    font-size: 11px;
    line-height: 16px;
  }

  .tag-field__arrow {
    flex: none;
    margin-left: auto;
    color: var(--color-text-tertiary);
    font-size: 12px;
    transition: transform 0.2s ease;
  }

  &.is-open .tag-field__arrow {
    transform: rotate(180deg);
  }
}

.tag-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;

  .tag-editor__group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .tag-editor__meta {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }

  .tag-editor__list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 240px;
    overflow-y: auto;

    &.is-suggest {
      max-height: 160px;
    }
  }

  .tag-row--suggest {
    width: 100%;
    border: none;
    background: none;
    text-align: left;
    cursor: pointer;

    &:hover .tag-row__add {
      color: var(--color-primary);
    }
  }

  .tag-row__add {
    flex: none;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .tag-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--radius-sm);

    &:hover {
      background: var(--color-bg-hover);
    }
  }

  .tag-row__text {
    flex: 1;
    min-width: 0;
    color: var(--color-text);
    font-size: 12px;
    line-height: 18px;
    word-break: break-all;
  }

  .tag-row__remove {
    display: inline-flex;
    flex: none;
    padding: 2px;
    border: none;
    background: none;
    color: var(--color-text-tertiary);
    cursor: pointer;

    &:hover {
      color: var(--color-danger);
    }
  }

  .tag-editor__empty {
    padding: 8px;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}
</style>
