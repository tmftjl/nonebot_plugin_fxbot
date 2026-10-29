<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { chatApi } from '@/api'
import type { ChatTenant } from '@/types/api'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const SCENE_LABELS: Record<string, string> = { group: '群聊', private: '私聊' }

const tenants = ref<ChatTenant[]>([])
const platform = ref('')
const bot = ref('')
const scene = ref('')
const target = ref('')

const split = (tenant: string): string[] => {
  const parts = tenant.split(':')
  return parts.length >= 4 ? [parts[0], parts[1], parts[2], parts.slice(3).join(':')] : ['', '', '', '']
}

const unique = (values: string[]) => [...new Set(values.filter(Boolean))]

const platforms = computed(() =>
  unique([...tenants.value.map((item) => split(item.tenant)[0]), platform.value]),
)

const bots = computed(() =>
  unique([
    ...tenants.value.filter((item) => split(item.tenant)[0] === platform.value).map((item) => split(item.tenant)[1]),
    bot.value,
  ]),
)

const scenes = computed(() =>
  unique([
    ...tenants.value
      .filter((item) => split(item.tenant)[0] === platform.value && split(item.tenant)[1] === bot.value)
      .map((item) => split(item.tenant)[2]),
    scene.value,
  ]),
)

const targets = computed(() =>
  unique([
    ...tenants.value
      .filter((item) => {
        const [itemPlatform, itemBot, itemScene] = split(item.tenant)
        return itemPlatform === platform.value && itemBot === bot.value && itemScene === scene.value
      })
      .map((item) => split(item.tenant)[3]),
    target.value,
  ]),
)

const anyChosen = computed(() => Boolean(platform.value || bot.value || scene.value || target.value))

const complete = computed(() => Boolean(platform.value && bot.value && scene.value && target.value))

const hint = computed(() => {
  if (!tenants.value.length) return '还没有可选会话，先让机器人聊几句再来选。'
  if (complete.value) return ''
  if (!anyChosen.value) return '全部留空：对所有会话生效'
  return '四项都选才生效，否则仍按全局处理'
})

const emitValue = () => {
  emit('update:modelValue', complete.value ? `${platform.value}:${bot.value}:${scene.value}:${target.value}` : '')
}

const pickPlatform = () => {
  bot.value = ''
  scene.value = ''
  target.value = ''
  emitValue()
}

const pickBot = () => {
  scene.value = ''
  target.value = ''
  emitValue()
}

const pickScene = () => {
  target.value = ''
  emitValue()
}

onMounted(async () => {
  const [currentPlatform, currentBot, currentScene, currentTarget] = split(props.modelValue)
  platform.value = currentPlatform
  bot.value = currentBot
  scene.value = currentScene
  target.value = currentTarget
  try {
    tenants.value = (await chatApi.tenants()).tenants || []
  } catch {
    tenants.value = []
  }
})
</script>

<template>
  <div class="chat-tenant">
    <div class="chat-tenant-grid">
      <label class="chat-tenant-field">
        <span class="chat-tenant-name">平台</span>
        <el-select
          :model-value="platform"
          placeholder="任意平台"
          clearable
          filterable
          @update:model-value="(v: string) => { platform = v || ''; pickPlatform() }"
        >
          <el-option v-for="item in platforms" :key="item" :label="item" :value="item" />
        </el-select>
      </label>
      <label class="chat-tenant-field">
        <span class="chat-tenant-name">机器人</span>
        <el-select
          :model-value="bot"
          placeholder="任意机器人"
          clearable
          filterable
          :disabled="!platform"
          @update:model-value="(v: string) => { bot = v || ''; pickBot() }"
        >
          <el-option v-for="item in bots" :key="item" :label="`Bot ${item}`" :value="item" />
        </el-select>
      </label>
      <label class="chat-tenant-field">
        <span class="chat-tenant-name">聊天类型</span>
        <el-select
          :model-value="scene"
          placeholder="任意类型"
          clearable
          :disabled="!bot"
          @update:model-value="(v: string) => { scene = v || ''; pickScene() }"
        >
          <el-option
            v-for="item in scenes"
            :key="item"
            :label="SCENE_LABELS[item] || item"
            :value="item"
          />
        </el-select>
      </label>
      <label class="chat-tenant-field">
        <span class="chat-tenant-name">会话</span>
        <el-select
          :model-value="target"
          placeholder="任意会话"
          clearable
          filterable
          :disabled="!scene"
          @update:model-value="(v: string) => { target = v || ''; emitValue() }"
        >
          <el-option v-for="item in targets" :key="item" :label="item" :value="item" />
        </el-select>
      </label>
    </div>
    <div v-if="hint" class="chat-tenant-hint" :class="{ 'is-warning': anyChosen && !complete }">
      {{ hint }}
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-tenant {
  width: 100%;
}

.chat-tenant-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 12px;
}

.chat-tenant-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.chat-tenant-name {
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1;
}

.chat-tenant-field :deep(.el-select) {
  width: 100%;
}

.chat-tenant-field :deep(.el-select__wrapper.is-disabled) {
  background: var(--color-bg);
}

.chat-tenant-hint {
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.chat-tenant-hint.is-warning {
  color: var(--color-warning);
}
</style>
