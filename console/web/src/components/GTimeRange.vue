<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ modelValue: string; placeholder?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const value = computed<string[] | null>(() => {
  const match = /^(\d{2}:\d{2})-(\d{2}:\d{2})$/.exec(props.modelValue || '')
  return match ? [match[1], match[2]] : null
})

const update = (next: string[] | null) => {
  emit(
    'update:modelValue',
    next && next.length === 2 && next[0] && next[1] ? `${next[0]}-${next[1]}` : '',
  )
}
</script>

<template>
  <div class="time-range">
    <el-time-picker
      :model-value="value"
      is-range
      format="HH:mm"
      value-format="HH:mm"
      :placeholder="placeholder || '不选按全天'"
      start-placeholder="开始"
      end-placeholder="结束"
      clearable
      style="width: 100%"
      @update:model-value="update"
    />
    <div v-if="!props.modelValue" class="time-range-hint">{{ placeholder || '不选按全天' }}</div>
  </div>
</template>

<style scoped lang="scss">
.time-range {
  width: 100%;
}

.time-range-hint {
  margin-top: 4px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
</style>
