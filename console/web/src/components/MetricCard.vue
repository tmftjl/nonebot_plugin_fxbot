<!-- -*- coding: utf-8 -*- -->
<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    icon?: Component
    tone?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
  }>(),
  { tone: 'primary', hint: '', icon: undefined },
)
</script>

<template>
  <article class="metric-card" :class="`is-${tone}`">
    <div class="metric-top">
      <span class="metric-label">{{ label }}</span>
      <span v-if="icon" class="metric-icon">
        <el-icon><component :is="icon" /></el-icon>
      </span>
    </div>
    <div class="metric-value">{{ value }}</div>
    <div v-if="hint" class="metric-hint">{{ hint }}</div>
  </article>
</template>

<style scoped lang="scss">
.metric-card {
  min-width: 0;
  padding: 16px 18px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  transition: border-color 0.15s ease, transform 0.15s ease;

  &:hover {
    border-color: var(--color-border-strong, var(--color-text-tertiary));
    transform: translateY(-1px);
  }
}

.metric-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.metric-label {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 16px;
}

.metric-value {
  margin-top: 10px;
  color: var(--color-text);
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  line-height: 1.15;
}

.metric-hint {
  margin-top: 4px;
  overflow: hidden;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-card.is-success .metric-icon {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
}

.metric-card.is-warning .metric-icon {
  background: color-mix(in srgb, var(--color-warning) 14%, transparent);
  color: var(--color-warning);
}

.metric-card.is-danger .metric-icon {
  background: color-mix(in srgb, var(--color-danger) 14%, transparent);
  color: var(--color-danger);
}

.metric-card.is-info .metric-icon {
  background: color-mix(in srgb, var(--color-info) 14%, transparent);
  color: var(--color-info);
}
</style>
