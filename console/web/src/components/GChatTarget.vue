<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { loadTenants } from '@/api/tenants'
import type { ChatTenant } from '@/types/api'

const props = withDefaults(
  defineProps<{
    modelValue: string
    platform?: string
    scene?: string
    placeholder?: string
  }>(),
  { platform: '', scene: '', placeholder: '留空表示该平台默认' },
)

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const tenants = ref<ChatTenant[]>([])

const split = (tenant: string): string[] => {
  const parts = tenant.split(':')
  return parts.length >= 4 ? [parts[0], parts[2], parts.slice(3).join(':')] : ['', '', '']
}

const options = computed(() => {
  const seen = new Set<string>()
  const list: { value: string; label: string }[] = []
  for (const item of tenants.value) {
    const [platform, scene, target] = split(String(item.tenant))
    if (props.platform && platform !== props.platform) continue
    if (props.scene && scene !== props.scene) continue
    if (!target || seen.has(target)) continue
    seen.add(target)
    list.push({ value: target, label: props.platform ? target : `${target} · ${platform}` })
  }
  if (props.modelValue && !seen.has(props.modelValue)) {
    list.unshift({ value: props.modelValue, label: props.modelValue })
  }
  return list
})

watch(
  () => [props.platform, props.scene],
  () => {
    if (props.modelValue) emit('update:modelValue', '')
  },
)

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
    <el-option v-for="item in options" :key="item.value" :label="item.label" :value="item.value" />
  </el-select>
</template>
