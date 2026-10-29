<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { configApi } from '@/api'
import ConfigCard from '@/components/ConfigCard.vue'
import type { ConfigTab } from '@/types/schema'

interface Props {
  activeTab?: string
}

const props = withDefaults(defineProps<Props>(), {
  activeTab: ''
})

const saving = ref(false)
const loaded = ref(false)
const configs = ref<Record<string, any>>({})
const tabs = ref<ConfigTab[]>([])
const currentTabKey = ref('')
const hasChanges = ref(false)

// 监听外部传入的 activeTab
watch(() => props.activeTab, (newVal) => {
  if (newVal) {
    currentTabKey.value = newVal
  }
}, { immediate: true })

const currentTab = () => tabs.value.find(t => t.key === currentTabKey.value)

const getTabData = () => {
  const dataKey = currentTab()?.dataKey
  if (!dataKey) return configs.value
  return dataKey.split('.').reduce((data: any, key) => data?.[key], configs.value) || {}
}

const getCardData = (cardKey: string) => {
  const parts = cardKey.split('.')
  let current: any = getTabData()
  for (const part of parts) {
    if (!current || typeof current !== 'object') return {}
    current = current[part]
  }
  return current || {}
}

const setCardData = (cardKey: string, data: Record<string, any>) => {
  const parts = cardKey.split('.')
  let current = getTabData()

  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i]
    if (!current[part] || typeof current[part] !== 'object') {
      current[part] = {}
    }
    current = current[part]
  }

  current[parts[parts.length - 1]] = data
  hasChanges.value = true
}

const loadData = async () => {
  try {
    const [tabsData, configData] = await Promise.all([
      configApi.getTabs(),
      configApi.getAll()
    ])
    tabs.value = tabsData || []
    configs.value = configData || {}

    // 如果没有指定tab，默认选第一个
    if (tabs.value.length && !currentTabKey.value) {
      currentTabKey.value = tabs.value[0].key
    }
    hasChanges.value = false
  } catch (e: any) {
    ElMessage.error('加载配置失败: ' + (e?.message || e))
  } finally {
    loaded.value = true
  }
}

const saveConfig = async () => {
  saving.value = true
  try {
    await configApi.save(configs.value)
    ElMessage.success('配置已保存')
    hasChanges.value = false
    await loadData()
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

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="config-view">
    <PageHeader :title="currentTab()?.title || '插件配置'" subtitle="修改后点击卡片右上角的保存按钮生效" />

    <div v-if="loaded && !tabs.length" class="empty-state">
      <el-empty description="暂无配置" />
    </div>

    <div v-else-if="loaded" class="cards-container">
      <transition name="fade" mode="out-in">
        <div :key="currentTabKey" class="tab-content">
          <template v-for="tab in tabs" :key="tab.key">
            <template v-if="currentTabKey === tab.key">
              <ConfigCard
                v-for="card in tab.cards"
                :key="card.key"
                :card="card"
                :model-value="getCardData(card.key)"
                @update:model-value="(data) => setCardData(card.key, data)"
              >
                <template #header-extra>
                  <el-button
                    type="primary"
                    size="small"
                    :loading="saving"
                    :disabled="!hasChanges"
                    @click="saveConfig"
                  >
                    保存
                  </el-button>
                </template>
              </ConfigCard>
            </template>
          </template>
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped lang="scss">
.config-view {
  padding: 24px 28px 32px;
}

.cards-container {
  width: 100%;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
