<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { loadTenants } from '@/api/tenants'
import type { ChatTenant } from '@/types/api'

const props = withDefaults(defineProps<{ modelValue: string; placeholder?: string }>(), {
  placeholder: '留空表示任意平台',
})

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const tenants = ref<ChatTenant[]>([])

const options = computed(() => {
  const platforms = tenants.value.map((item) => String(item.tenant).split(':')[0])
  return [...new Set([...platforms, props.modelValue].filter(Boolean))]
})

onMounted(async () => {
  tenants.value = await loadTenants()
})
</script>

<template>
  <el-select
    :model-value="modelValue"
    :placeholder="placeholder"
    filterable
    allow-create
    default-first-option
    clearable
    style="width: 100%"
    @update:model-value="(v: string) => emit('update:modelValue', v || '')"
  >
    <el-option v-for="item in options" :key="item" :label="item" :value="item" />
  </el-select>
</template>
