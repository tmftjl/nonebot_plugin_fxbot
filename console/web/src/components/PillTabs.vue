<script setup lang="ts" generic="T extends string">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: T
    items: readonly { key: T; label: string; hint?: string }[]
    size?: 'default' | 'small'
  }>(),
  { size: 'default' },
)

const emit = defineEmits<{ 'update:modelValue': [value: T] }>()

const wrapRef = ref<HTMLElement | null>(null)
const buttons: HTMLButtonElement[] = []
const left = ref(0)
const width = ref(0)
const ready = ref(false)
const wrapped = ref(false)

const setButton = (el: unknown, index: number) => {
  if (el instanceof HTMLButtonElement) buttons[index] = el
}

const measure = () => {
  const index = props.items.findIndex((item) => item.key === props.modelValue)
  const button = buttons[index]
  if (!button) return
  left.value = button.offsetLeft
  width.value = button.offsetWidth
  ready.value = true
  const firstTop = buttons[0]?.offsetTop
  wrapped.value = buttons.some((item) => item && item.offsetTop !== firstTop)
}

watch(
  () => props.modelValue,
  () => nextTick(measure),
)
watch(
  () => props.items,
  () => nextTick(measure),
)

let observer: ResizeObserver | null = null

onMounted(() => {
  nextTick(measure)
  observer = new ResizeObserver(measure)
  if (wrapRef.value) observer.observe(wrapRef.value)
  window.addEventListener('resize', measure)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <div ref="wrapRef" class="pill-tabs" :class="[`is-${size}`, { 'is-wrapped': wrapped }]">
    <span
      class="pill-indicator"
      :class="{ ready }"
      :style="{ width: `${width}px`, transform: `translateX(${left}px)` }"
    />
    <button
      v-for="(item, index) in items"
      :key="item.key"
      :ref="(el) => setButton(el, index)"
      type="button"
      class="pill-tab"
      :class="{ active: modelValue === item.key }"
      :title="item.hint"
      @click="emit('update:modelValue', item.key)"
    >
      {{ item.label }}
    </button>
  </div>
</template>

<style scoped lang="scss">
.pill-tabs {
  --pill-pad: 4px;
  --pill-gap: 2px;
  position: relative;
  display: flex;
  flex-wrap: wrap;
  gap: var(--pill-gap);
  padding: var(--pill-pad);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);

  &.is-small {
    --pill-pad: 2px;
    --pill-gap: 1px;
  }
}

.pill-indicator {
  position: absolute;
  top: var(--pill-pad);
  bottom: var(--pill-pad);
  left: 0;
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  opacity: 0;
  transition:
    transform 0.28s cubic-bezier(0.4, 0, 0.2, 1),
    width 0.28s cubic-bezier(0.4, 0, 0.2, 1),
    opacity 0.2s ease;

  &.ready {
    opacity: 1;
  }
}

/* 换行后滑块无法跨行，直接给选中项上底色 */
.pill-tabs.is-wrapped .pill-indicator {
  display: none;
}

.pill-tabs.is-wrapped .pill-tab.active {
  background: var(--color-primary-soft);
}

.pill-tab {
  position: relative;
  z-index: 1;
  padding: 6px 14px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition:
    color 0.2s ease,
    background-color 0.2s ease;

  &:hover {
    background: color-mix(in srgb, var(--color-primary) 7%, transparent);
    color: var(--color-text);
  }

  &:focus-visible {
    outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
    outline-offset: 2px;
  }

  &.active {
    color: var(--color-primary);
    font-weight: 600;
    transition-delay: 0s;
  }

  .is-small & {
    padding: 3px 10px;
    font-size: 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pill-indicator,
  .pill-tab {
    transition: none;
  }
}
</style>
