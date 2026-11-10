<script setup lang="ts">
import { traceItemKind, traceItemLabel, traceItemText, traceJson, traceRecords, type TraceRecord } from '@/utils/modelTrace'

defineProps<{ items: TraceRecord[]; emptyText?: string }>()

const functionOf = (item: TraceRecord): TraceRecord =>
  item.function && typeof item.function === 'object' ? item.function as TraceRecord : item
</script>

<template>
  <div v-if="items.length" class="item-timeline">
    <article v-for="(item, index) in items" :key="index" class="context-item" :data-kind="traceItemKind(item)">
      <header>
        <span class="item-role">{{ traceItemLabel(item) }}</span>
        <span v-if="item.attemptLabel" class="item-attempt">{{ item.attemptLabel }}</span>
        <span class="item-index">{{ String(index + 1).padStart(2, '0') }}</span>
      </header>
      <template v-if="item.type === 'function_call'">
        <div class="tool-name">{{ item.name || '未命名工具' }}</div>
        <pre class="item-code">{{ traceJson(item.arguments) }}</pre>
      </template>
      <p v-else-if="traceItemText(item)" class="item-prose">{{ traceItemText(item) }}</p>
      <p v-else class="item-placeholder">{{ item.type === 'reasoning' ? '该推理条目未返回可读文字，原生数据保留在完整快照中。' : '此条目没有可显示的文字。' }}</p>
      <div v-for="(call, callIndex) in traceRecords(item.tool_calls)" :key="callIndex" class="nested-tool">
        <span>工具调用</span><strong>{{ functionOf(call).name }}</strong>
        <pre class="item-code">{{ traceJson(functionOf(call).arguments) }}</pre>
      </div>
      <details class="item-source">
        <summary>原始条目</summary>
        <pre class="item-code">{{ traceJson(item) }}</pre>
      </details>
    </article>
  </div>
  <div v-else class="timeline-empty">{{ emptyText || '没有可展示的内容' }}</div>
</template>

<style scoped>
.item-timeline {
  display: grid;
  gap: 14px;
}

.context-item {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 9px;
  background: var(--color-bg-secondary);
}

.context-item header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--color-border-light);
}

.item-role {
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 600;
}

.item-index {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.item-attempt {
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.item-prose {
  margin: 0;
  padding: 18px;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.95;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.item-placeholder, .timeline-empty {
  padding: 18px;
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.8;
}

.context-item[data-kind="reasoning"] {
  border-left: 3px solid var(--color-primary);
}

.context-item[data-kind="reasoning"] .item-prose {
  color: var(--color-text-secondary);
}

.context-item[data-kind="system"], .context-item[data-kind="developer"] {
  background: var(--color-bg-card);
}

.context-item[data-kind="error"] {
  border-left: 3px solid var(--color-danger);
}

.context-item[data-kind="error"] .item-role {
  color: var(--color-danger);
}

.context-item[data-kind="function_call"] .item-role, .context-item[data-kind="tool"] .item-role {
  color: var(--color-warning);
}

.tool-name {
  padding: 14px 16px 0;
  color: var(--color-text);
  font-family: ui-monospace, monospace;
  font-size: 13px;
  font-weight: 600;
}

.item-code {
  max-height: 380px;
  margin: 12px 16px 16px;
  padding: 14px;
  overflow: auto;
  border-radius: 6px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font: 11px/1.8 ui-monospace, monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.item-source {
  border-top: 1px solid var(--color-border-light);
}

.item-source summary {
  padding: 9px 16px;
  color: var(--color-text-tertiary);
  font-size: 10px;
  cursor: pointer;
}

.nested-tool {
  margin: 0 16px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: 6px;
}

.nested-tool > span, .nested-tool > strong {
  display: inline-block;
  margin: 10px 0 0 12px;
  font-size: 11px;
  color: var(--color-text-secondary);
}
</style>
