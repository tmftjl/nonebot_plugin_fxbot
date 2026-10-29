<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, Check, Search, User, UserFilled } from '@element-plus/icons-vue'
import type { ChatTenant } from '@/types/api'

const props = defineProps<{
  modelValue: string
  tenants: ChatTenant[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [value: string]
}>()

interface TenantView {
  tenant: string
  messages: number
  scene: 'group' | 'private' | 'other'
  sceneLabel: string
  platform: string
  bot: string
  short: string
  searchText: string
}

const open = ref(false)
const query = ref('')
const root = ref<HTMLElement | null>(null)

const parse = (tenant: string, messages: number): TenantView => {
  const parts = tenant.split(':')
  const scene = parts[2]
  const target = parts.slice(3).join(':')
  const view: TenantView = parts.length >= 4
    ? {
        tenant,
        messages,
        scene: scene === 'group' ? 'group' : scene === 'private' ? 'private' : 'other',
        sceneLabel: scene === 'group' ? '群聊' : scene === 'private' ? '私聊' : '会话',
        platform: parts[0],
        bot: parts[1],
        short: target.length > 14 ? `${target.slice(0, 6)}…${target.slice(-6)}` : target,
        searchText: '',
      }
    : {
        tenant,
        messages,
        scene: 'other',
        sceneLabel: '会话',
        platform: tenant,
        bot: '',
        short: tenant,
        searchText: '',
      }
  view.searchText = `${view.tenant} ${view.sceneLabel} ${view.platform} ${view.bot}`.toLowerCase()
  return view
}

const rows = computed(() => props.tenants.map((item) => parse(item.tenant, item.messages)))
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return keyword ? rows.value.filter((row) => row.searchText.includes(keyword)) : rows.value
})
const customTenant = computed(() => {
  const value = query.value.trim()
  return value.includes(':') && !rows.value.some((row) => row.tenant === value) ? value : ''
})
const current = computed(() => {
  const matched = rows.value.find((row) => row.tenant === props.modelValue)
  return matched || parse(props.modelValue, 0)
})
const currentMeta = computed(() => {
  const parts = [current.value.platform]
  if (current.value.bot) parts.push(`Bot ${current.value.bot}`)
  if (current.value.messages) parts.push(`${current.value.messages} 条`)
  return parts.join(' · ')
})

const pick = (value: string) => {
  open.value = false
  query.value = ''
  if (value === props.modelValue) return
  emit('update:modelValue', value)
  emit('change', value)
}

const submitSearch = () => {
  if (filtered.value.length) pick(filtered.value[0].tenant)
  else if (customTenant.value) pick(customTenant.value)
}

const onDocumentClick = (event: MouseEvent) => {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}
const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') open.value = false
}

watch(open, (value) => {
  if (value) query.value = ''
})

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
  <div ref="root" class="tenant-picker">
    <button
      type="button"
      class="tenant-trigger"
      :class="{ open }"
      :title="modelValue || '选择会话'"
      @click="open = !open"
    >
      <span class="tenant-avatar" :class="current.scene">
        <el-icon><UserFilled v-if="current.scene === 'group'" /><User v-else /></el-icon>
      </span>
      <span class="tenant-current">
        <span class="tenant-current-name">
          {{ modelValue ? `${current.sceneLabel} · ${current.short}` : '选择会话' }}
        </span>
        <span class="tenant-current-meta">
          {{ modelValue ? currentMeta : '按群聊、私聊或 session_id 筛选' }}
        </span>
      </span>
      <el-icon class="tenant-chevron"><ArrowDown /></el-icon>
    </button>

    <div v-if="open" class="tenant-pop">
      <div class="tenant-search">
        <el-icon><Search /></el-icon>
        <input
          v-model="query"
          placeholder="搜索群号、用户或 session_id"
          @keydown.enter.prevent="submitSearch"
        >
        <button v-if="query" type="button" class="tenant-search-clear" @click="query = ''">×</button>
      </div>
      <div class="tenant-list">
        <button
          v-for="row in filtered"
          :key="row.tenant"
          type="button"
          class="tenant-option"
          :class="{ active: row.tenant === modelValue }"
          :title="row.tenant"
          @click="pick(row.tenant)"
        >
          <span class="tenant-avatar" :class="row.scene">
            <el-icon><UserFilled v-if="row.scene === 'group'" /><User v-else /></el-icon>
          </span>
          <span class="tenant-option-main">
            <span class="tenant-option-title">
              <span class="tenant-option-name">{{ row.sceneLabel }} · {{ row.short }}</span>
              <el-icon v-if="row.tenant === modelValue" class="tenant-check"><Check /></el-icon>
            </span>
            <span class="tenant-option-meta">
              {{ row.platform }}<template v-if="row.bot"> · Bot {{ row.bot }}</template> · {{ row.messages }} 条
            </span>
          </span>
        </button>
        <div v-if="!filtered.length" class="tenant-empty">没有匹配的会话</div>
      </div>
      <button v-if="customTenant" type="button" class="tenant-custom" @click="pick(customTenant)">
        使用 session_id「{{ customTenant }}」
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.tenant-picker {
  position: relative;
  flex: 1;
  max-width: 520px;
}

.tenant-trigger {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 48px;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &:hover {
    border-color: var(--color-text-tertiary);
  }

  &.open {
    border-color: var(--color-primary);
    box-shadow: 0 0 0 1px var(--color-primary) inset;
  }
}

.tenant-avatar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 15px;

  &.group {
    background: var(--color-primary-soft);
    color: var(--color-primary);
  }
}

.tenant-current {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.tenant-current-name {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tenant-current-meta {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tenant-chevron {
  flex: none;
  color: var(--color-text-tertiary);
  transition: transform 0.15s ease;

  .tenant-trigger.open & {
    transform: rotate(180deg);
  }
}

.tenant-pop {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  width: 100%;
  min-width: 340px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  box-shadow: var(--shadow-lg);
}

.tenant-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-tertiary);

  input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    background: transparent;
    color: var(--color-text);
    font-family: inherit;
    font-size: 13px;

    &::placeholder {
      color: var(--color-text-tertiary);
    }
  }
}

.tenant-search-clear {
  flex: none;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;

  &:hover {
    color: var(--color-text);
  }
}

.tenant-list {
  max-height: 320px;
  overflow-y: auto;
  padding: 6px;
  scrollbar-width: thin;
}

.tenant-option {
  display: flex;
  align-items: center;
  gap: 10px;
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

    .tenant-option-name {
      color: var(--color-primary);
    }
  }
}

.tenant-option-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.tenant-option-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tenant-option-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tenant-check {
  flex: none;
  color: var(--color-primary);
  font-size: 14px;
}

.tenant-option-meta {
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tenant-empty {
  padding: 24px 12px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.tenant-custom {
  display: block;
  width: calc(100% - 12px);
  margin: 0 6px 6px;
  padding: 8px 10px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: left;
  word-break: break-all;
  cursor: pointer;

  &:hover {
    border-color: var(--color-primary);
    color: var(--color-primary);
  }
}
</style>
