<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import { computed } from 'vue'

interface TrendPoint {
  date: string
  sent: number
  group?: number
  private?: number
}

const props = withDefaults(
  defineProps<{
    items: TrendPoint[]
    height?: number
    showAxis?: boolean
  }>(),
  { height: 180, showAxis: true },
)

const count = computed(() => props.items.length)
const maxValue = computed(() => Math.max(1, ...props.items.map((item) => item.sent)))
const totalSent = computed(() => props.items.reduce((sum, item) => sum + item.sent, 0))

const coords = computed(() =>
  props.items.map((item, index) => ({
    x: count.value > 1 ? (index / (count.value - 1)) * 100 : 50,
    y: 94 - (item.sent / maxValue.value) * 88,
    point: item,
  })),
)

const linePath = computed(() => {
  if (!coords.value.length) return ''
  return coords.value
    .map((coord, index) => `${index === 0 ? 'M' : 'L'}${coord.x.toFixed(2)},${coord.y.toFixed(2)}`)
    .join(' ')
})

const areaPath = computed(() => {
  if (!coords.value.length) return ''
  const first = coords.value[0]
  const last = coords.value[coords.value.length - 1]
  return `${linePath.value} L${last.x.toFixed(2)},94 L${first.x.toFixed(2)},94 Z`
})

const axisLabels = computed(() => {
  const size = count.value
  if (size < 2) return coords.value.map((coord) => ({ ...coord, text: coord.point.date.slice(5) }))
  const indexes = [0, Math.round((size - 1) / 3), Math.round(((size - 1) * 2) / 3), size - 1]
  return [...new Set(indexes)].map((index) => ({
    ...coords.value[index],
    text: coords.value[index].point.date.slice(5),
  }))
})
</script>

<template>
  <div class="trend-chart" :class="{ 'with-axis': showAxis }" :style="{ height: `${height}px` }">
    <svg class="trend-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--color-primary)" stop-opacity="0.3" />
          <stop offset="100%" stop-color="var(--color-primary)" stop-opacity="0.02" />
        </linearGradient>
      </defs>
      <line
        v-for="ratio in [25, 50, 75]"
        :key="ratio"
        :x1="0"
        :x2="100"
        :y1="ratio"
        :y2="ratio"
        class="trend-grid"
        vector-effect="non-scaling-stroke"
      />
      <path v-if="areaPath" :d="areaPath" fill="url(#trend-fill)" />
      <path
        v-if="linePath"
        :d="linePath"
        class="trend-line"
        fill="none"
        stroke="var(--color-primary)"
        stroke-width="2"
        stroke-linejoin="round"
        stroke-linecap="round"
        vector-effect="non-scaling-stroke"
      />
    </svg>

    <span
      v-for="coord in coords"
      :key="coord.point.date"
      class="trend-dot"
      :style="{ left: `${coord.x}%`, top: `${coord.y}%` }"
      :title="`${coord.point.date} · ${coord.point.sent} 条`"
    />

    <div v-if="showAxis" class="trend-axis">
      <span
        v-for="tick in axisLabels"
        :key="tick.point.date"
        class="trend-tick"
        :style="{ left: `${tick.x}%` }"
      >
        {{ tick.text }}
      </span>
    </div>

    <div v-if="!totalSent" class="trend-empty">暂无消息记录</div>
  </div>
</template>

<style scoped lang="scss">
.trend-chart {
  position: relative;
  width: 100%;

  &.with-axis {
    margin-bottom: 18px;
  }
}

.trend-svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.trend-grid {
  stroke: var(--color-border-light);
  stroke-width: 1;
  stroke-dasharray: 3 3;
}

.trend-line {
  filter: drop-shadow(0 2px 6px rgba(224, 111, 6, 0.18));
}

.trend-dot {
  position: absolute;
  width: 6px;
  height: 6px;
  border: 2px solid var(--color-bg-card);
  border-radius: 50%;
  background: var(--color-primary);
  transform: translate(-50%, -50%);
  transition: transform 0.15s ease;

  &:hover {
    transform: translate(-50%, -50%) scale(1.6);
  }
}

.trend-axis {
  position: absolute;
  right: 0;
  bottom: -18px;
  left: 0;
  height: 14px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.trend-tick {
  position: absolute;
  transform: translateX(-50%);
  white-space: nowrap;

  &:first-child {
    transform: translateX(0);
  }

  &:last-child {
    transform: translateX(-100%);
  }
}

.trend-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
  font-size: 12px;
  pointer-events: none;
}
</style>
