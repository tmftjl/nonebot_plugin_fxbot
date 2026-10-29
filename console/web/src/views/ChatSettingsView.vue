<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import ConfigCardPicker from '@/components/ConfigCardPicker.vue'
import { configApi } from '@/api'
import type { ConfigCard as ConfigCardType } from '@/types/schema'

const CATEGORY_ORDER = ['基础', '聊天', '能力', '学习', '记忆', '视觉', '消息接收', '后处理']

const saving = ref(false)
const loaded = ref(false)
const configs = ref<Record<string, any>>({})
const cards = ref<ConfigCardType[]>([])
const hasChanges = ref(false)
const activeCategory = ref('')
const activeCardKey = ref('')

// 对话设置卡片对应 system 配置文件里的 chat 段
const getCardData = (cardKey: string) => {
  let current: any = configs.value.system
  for (const part of cardKey.split('.')) {
    if (!current || typeof current !== 'object') return {}
    current = current[part]
  }
  return current || {}
}

const setCardData = (cardKey: string, data: Record<string, any>) => {
  const parts = cardKey.split('.')
  if (!configs.value.system || typeof configs.value.system !== 'object') configs.value.system = {}
  let current: any = configs.value.system
  for (let i = 0; i < parts.length - 1; i += 1) {
    const part = parts[i]
    if (!current[part] || typeof current[part] !== 'object') current[part] = {}
    current = current[part]
  }
  current[parts[parts.length - 1]] = data
  hasChanges.value = true
}

const categories = computed(() => {
  const groups = new Map<string, ConfigCardType[]>()
  for (const card of cards.value) {
    const key = card.category || '其他'
    const group = groups.get(key) || []
    group.push(card)
    groups.set(key, group)
  }
  const ordered = CATEGORY_ORDER.filter((key) => groups.has(key))
  const extra = [...groups.keys()].filter((key) => !CATEGORY_ORDER.includes(key))
  return [...ordered, ...extra].map((key) => ({ key, cards: groups.get(key) || [] }))
})

const currentCards = computed(
  () => categories.value.find((item) => item.key === activeCategory.value)?.cards || [],
)

const cardTabs = computed(() =>
  currentCards.value.map((card) => ({ key: card.key, label: card.title, hint: card.desc })),
)

const selectCategory = (key: string) => {
  activeCategory.value = key
}

watch(activeCategory, () => {
  if (!currentCards.value.some((card) => card.key === activeCardKey.value)) {
    activeCardKey.value = currentCards.value[0]?.key || ''
  }
})

const loadData = async () => {
  try {
    const [tab, configData] = await Promise.all([configApi.getChatTab(), configApi.getAll()])
    cards.value = tab?.cards || []
    configs.value = configData || {}
    hasChanges.value = false
    const firstCategory = categories.value[0]
    activeCategory.value = firstCategory?.key || ''
    activeCardKey.value = firstCategory?.cards[0]?.key || ''
  } catch (e: any) {
    ElMessage.error('加载对话设置失败: ' + (e?.message || e))
  } finally {
    loaded.value = true
  }
}

const saveConfig = async () => {
  saving.value = true
  try {
    await configApi.save(configs.value)
    ElMessage.success('对话设置已保存')
    hasChanges.value = false
  } catch (e: any) {
    if (e?.response?.status === 409) {
      ElMessage.error('配置已在其他页面被修改，请刷新后重试')
      await loadData()
    } else {
      ElMessage.error('保存失败: ' + (e?.message || e))
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="chat-settings-view">
    <PageHeader title="对话设置" subtitle="按分类管理聊天与回复参数；保存后立即热加载">
      <template #actions>
        <el-button type="primary" :loading="saving" :disabled="!hasChanges" @click="saveConfig">保存</el-button>
      </template>
    </PageHeader>

    <div v-if="loaded && !cards.length" class="empty-state">
      <el-empty description="暂无配置" />
    </div>

    <div v-else-if="loaded" class="settings-layout">
      <aside class="category-nav">
        <button
          v-for="category in categories"
          :key="category.key"
          type="button"
          class="category-item"
          :class="{ active: activeCategory === category.key }"
          @click="selectCategory(category.key)"
        >
          <span class="category-name">{{ category.key }}</span>
          <span class="category-count">{{ category.cards.length }}</span>
        </button>
      </aside>

      <section class="settings-main">
        <div v-if="cardTabs.length > 1" class="settings-head">
          <ConfigCardPicker v-model="activeCardKey" :items="cardTabs" />
        </div>
        <ConfigCard
          v-for="card in currentCards"
          v-show="card.key === activeCardKey"
          :key="card.key"
          :card="card"
          :model-value="getCardData(card.key)"
          @update:model-value="(data) => setCardData(card.key, data)"
        />
      </section>
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-settings-view {
  padding: 24px 28px 32px;
}

.settings-layout {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.category-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

.category-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;

  &:hover {
    background: var(--color-bg-tertiary);
    color: var(--color-text);
  }

  &.active {
    background: var(--color-primary-soft);
    color: var(--color-primary);
    font-weight: 600;
  }
}

.category-count {
  flex: none;
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 400;
}

.settings-main {
  min-width: 0;
}

.settings-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

@media (max-width: 960px) {
  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .category-nav {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .category-item {
    flex: 1 1 auto;
    justify-content: center;
    gap: 6px;
  }
}
</style>
