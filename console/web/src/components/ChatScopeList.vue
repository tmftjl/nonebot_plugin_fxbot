<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChatDotRound, Search, User, UserFilled } from '@element-plus/icons-vue'
import type { ChatTenant } from '@/types/api'

const props = withDefaults(
  defineProps<{
    modelValue: string
    tenants: ChatTenant[]
    title?: string
    showStats?: boolean
  }>(),
  { title: '聊天范围', showStats: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [value: string]
}>()

type Scope = 'all' | 'group' | 'private'

interface ScopeRow {
  tenant: string
  messages: number
  learn: number
  use: number
  scene: 'group' | 'private' | 'other'
  sceneLabel: string
  platform: string
  short: string
}

const scope = ref<Scope>('all')
const keyword = ref('')

const scopeOptions: { key: Scope; label: string }[] = [
  { key: 'private', label: '聊天' },
  { key: 'group', label: '组' },
  { key: 'all', label: '全部' },
]

const parse = (item: ChatTenant): ScopeRow => {
  const parts = item.tenant.split(':')
  const scene = parts[2]
  const target = parts.slice(3).join(':')
  if (parts.length < 4) {
    return {
      tenant: item.tenant,
      messages: item.messages,
      learn: item.learn_count || 0,
      use: item.use_count || 0,
      scene: 'other',
      sceneLabel: '会话',
      platform: item.tenant,
      short: item.tenant,
    }
  }
  return {
    tenant: item.tenant,
    messages: item.messages,
    learn: item.learn_count || 0,
    use: item.use_count || 0,
    scene: scene === 'group' ? 'group' : scene === 'private' ? 'private' : 'other',
    sceneLabel: scene === 'group' ? '群聊' : scene === 'private' ? '私聊' : '会话',
    platform: parts[0],
    short: target.length > 12 ? `${target.slice(0, 5)}…${target.slice(-5)}` : target,
  }
}

const rows = computed(() => props.tenants.map(parse))
const scoped = computed(() => {
  const visible = scope.value === 'all' ? rows.value : rows.value.filter((row) => row.scene === scope.value)
  const query = keyword.value.trim().toLowerCase()
  if (!query) return visible
  return visible.filter(
    (row) =>
      row.tenant.toLowerCase().includes(query) ||
      row.short.toLowerCase().includes(query) ||
      row.sceneLabel.includes(query),
  )
})
const totals = computed(() =>
  rows.value.reduce(
    (acc, row) => ({ learn: acc.learn + row.learn, use: acc.use + row.use }),
    { learn: 0, use: 0 },
  ),
)

const pick = (value: string) => {
  if (value === props.modelValue) return
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <aside class="scope-panel">
    <div class="scope-head">
      <span class="scope-title">{{ title }}</span>
      <span class="scope-count">{{ scoped.length }} 个会话</span>
    </div>

    <div class="scope-switch">
      <button
        v-for="option in scopeOptions"
        :key="option.key"
        type="button"
        class="scope-switch-item"
        :class="{ active: scope === option.key }"
        @click="scope = option.key"
      >
        {{ option.label }}
      </button>
    </div>

    <div class="scope-search">
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索会话 ID"
        :prefix-icon="Search"
        clearable
      />
    </div>

    <div class="scope-list">
      <button
        v-for="row in scoped"
        :key="row.tenant"
        type="button"
        class="scope-row"
        :class="{ active: row.tenant === modelValue }"
        :title="row.tenant"
        @click="pick(row.tenant)"
      >
        <span class="scope-avatar" :class="row.scene">
          <el-icon>
            <UserFilled v-if="row.scene === 'group'" />
            <User v-else-if="row.scene === 'private'" />
            <ChatDotRound v-else />
          </el-icon>
        </span>
        <span class="scope-body">
          <span class="scope-name">{{ row.sceneLabel }} · {{ row.short }}</span>
          <span class="scope-meta">
            <span>{{ row.messages }} 条消息</span>
            <template v-if="showStats">
              <span>学习 {{ row.learn }}</span>
              <span>使用 {{ row.use }}</span>
            </template>
          </span>
        </span>
      </button>
      <div v-if="!scoped.length" class="scope-empty">没有匹配的会话</div>
    </div>

    <div v-if="showStats" class="scope-foot">
      <span class="scope-foot-label">总学习</span>
      <span class="scope-foot-value">{{ totals.learn }}</span>
      <span class="scope-foot-label">总使用</span>
      <span class="scope-foot-value">{{ totals.use }}</span>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.scope-panel {
  display: flex;
  flex-direction: column;
  align-self: start;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

.scope-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 16px 10px;
}

.scope-title {
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
}

.scope-count {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.scope-switch {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
  margin: 0 12px 10px;
  padding: 3px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
}

.scope-switch-item {
  padding: 5px 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;

  &:hover {
    color: var(--color-text);
  }

  &.active {
    background: var(--color-bg-card);
    color: var(--color-text);
    font-weight: 600;
    box-shadow: var(--shadow-sm);
  }
}

.scope-search {
  padding: 0 12px 10px;
}

.scope-list {
  flex: 1;
  max-height: 520px;
  overflow-y: auto;
  padding: 0 8px 8px;
  scrollbar-width: thin;
}

.scope-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
  }

  &.active {
    background: var(--color-primary-soft);

    .scope-name {
      color: var(--color-primary);
    }
  }
}

.scope-avatar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 14px;

  &.group {
    background: var(--color-primary-soft);
    color: var(--color-primary);
  }
}

.scope-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.scope-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.scope-empty {
  padding: 24px 12px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.scope-foot {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 10px 16px 14px;
  border-top: 1px solid var(--color-border-light);
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.scope-foot-value {
  margin-right: 8px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

@media (max-width: 960px) {
  .scope-list {
    max-height: 240px;
  }
}
</style>
