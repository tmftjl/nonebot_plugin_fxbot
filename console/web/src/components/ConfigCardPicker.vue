<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowDown, Check } from '@element-plus/icons-vue'

const props = defineProps<{
  modelValue: string
  items: readonly { key: string; label: string; hint?: string }[]
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)

const current = computed(
  () => props.items.find((item) => item.key === props.modelValue) || props.items[0],
)

const pick = (key: string) => {
  open.value = false
  if (key !== props.modelValue) emit('update:modelValue', key)
}

const onDocumentClick = (event: MouseEvent) => {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}
const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="card-picker">
    <button
      type="button"
      class="card-picker-trigger"
      :class="{ open }"
      :title="current?.hint || current?.label"
      @click="open = !open"
    >
      <span class="card-picker-current">{{ current?.label || '选择设置项' }}</span>
      <el-icon class="card-picker-chevron"><ArrowDown /></el-icon>
    </button>

    <div v-if="open" class="card-picker-pop">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="card-picker-option"
        :class="{ active: item.key === modelValue }"
        :title="item.hint"
        @click="pick(item.key)"
      >
        <span class="card-picker-option-main">
          <span class="card-picker-option-title">
            {{ item.label }}
            <el-icon v-if="item.key === modelValue" class="card-picker-check"><Check /></el-icon>
          </span>
          <span v-if="item.hint" class="card-picker-option-hint">{{ item.hint }}</span>
        </span>
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.card-picker {
  position: relative;
}

.card-picker-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 5px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
  color: var(--color-text);
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &:hover {
    border-color: var(--color-text-tertiary);
  }

  &.open {
    border-color: var(--color-primary);
    box-shadow: 0 0 0 1px var(--color-primary) inset;
  }
}

.card-picker-chevron {
  flex: none;
  color: var(--color-text-tertiary);
  font-size: 12px;
  transition: transform 0.15s ease;

  .card-picker-trigger.open & {
    transform: rotate(180deg);
  }
}

.card-picker-pop {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 300px;
  max-height: 360px;
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  box-shadow: var(--shadow-lg);
  scrollbar-width: thin;
}

.card-picker-option {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  text-align: left;

  &:hover {
    background: var(--color-bg-tertiary);
  }

  &.active {
    background: var(--color-primary-soft);
  }
}

.card-picker-option-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.card-picker-option-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;

  .card-picker-option.active & {
    color: var(--color-primary);
  }
}

.card-picker-check {
  flex: none;
  color: var(--color-primary);
  font-size: 13px;
}

.card-picker-option-hint {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
