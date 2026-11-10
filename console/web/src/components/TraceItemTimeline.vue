<script setup lang="ts">
import { traceFields, traceItemKind, traceItemLabel, traceItemText, traceRecords, type TraceRecord } from '@/utils/modelTrace'

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
        <dl v-if="traceFields(item.arguments).length" class="item-fields">
          <div v-for="(field, fieldIndex) in traceFields(item.arguments)" :key="fieldIndex" :class="{ 'field-plain': !field.key }">
            <dt v-if="field.key">{{ field.key }}</dt>
            <dd>{{ field.value }}</dd>
          </div>
        </dl>
      </template>
      <p v-else-if="traceItemText(item)" class="item-prose">{{ traceItemText(item) }}</p>
      <p v-else class="item-placeholder">{{ item.type === 'reasoning' ? '该推理条目未返回可读文字，可能由模型或提供方省略。' : '此条目没有可显示的文字。' }}</p>
      <div v-for="(call, callIndex) in traceRecords(item.tool_calls)" :key="callIndex" class="nested-tool">
        <span>工具调用</span><strong>{{ functionOf(call).name }}</strong>
        <dl v-if="traceFields(functionOf(call).arguments).length" class="item-fields">
          <div v-for="(field, fieldIndex) in traceFields(functionOf(call).arguments)" :key="fieldIndex" :class="{ 'field-plain': !field.key }">
            <dt v-if="field.key">{{ field.key }}</dt>
            <dd>{{ field.value }}</dd>
          </div>
        </dl>
      </div>
    </article>
  </div>
  <div v-else class="timeline-empty">{{ emptyText || '没有可展示的内容' }}</div>
</template>

<style scoped>
.item-timeline {
  display: grid;
  gap: 10px;
}

.context-item {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: 10px;
  background: var(--color-bg-secondary);
}

.context-item header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-card);
}

.item-role {
  color: var(--color-text-secondary);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.item-index {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font: 10px ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
}

.item-attempt {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-size: 9px;
}

.item-prose {
  margin: 0;
  padding: 13px 16px 15px;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.item-placeholder, .timeline-empty {
  padding: 14px 16px;
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.8;
}

.timeline-empty {
  border: 1px dashed var(--color-border);
  border-radius: 10px;
  text-align: center;
}

.context-item[data-kind="reasoning"] {
  border-color: transparent;
  border-left: 2px solid var(--color-primary);
  background: var(--color-primary-soft);
}

.context-item[data-kind="reasoning"] header {
  border-bottom-color: transparent;
  background: transparent;
}

.context-item[data-kind="reasoning"] .item-role {
  color: var(--color-primary);
}

.context-item[data-kind="reasoning"] .item-prose {
  color: var(--color-text-secondary);
}

.context-item[data-kind="system"], .context-item[data-kind="developer"] {
  background: var(--color-bg-card);
}

.context-item[data-kind="error"] {
  border-left: 2px solid var(--color-danger);
}

.context-item[data-kind="error"] .item-role {
  color: var(--color-danger);
}

.context-item[data-kind="function_call"] .item-role, .context-item[data-kind="tool"] .item-role {
  color: var(--color-warning);
}

.tool-name {
  padding: 12px 14px 0;
  color: var(--color-text);
  font-family: ui-monospace, monospace;
  font-size: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.item-fields {
  display: grid;
  gap: 6px;
  margin: 10px 14px 14px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  background: var(--color-bg-card);
}

.item-fields > div {
  display: grid;
  grid-template-columns: minmax(0, 132px) minmax(0, 1fr);
  gap: 12px;
  line-height: 1.7;
}

.item-fields dt {
  color: var(--color-text-tertiary);
  font: 10.5px/1.7 ui-monospace, monospace;
  overflow-wrap: anywhere;
}

.item-fields dd {
  grid-column: 2;
  margin: 0;
  color: var(--color-text);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.item-fields .field-plain dd {
  grid-column: 1 / -1;
}

.nested-tool {
  margin: 0 14px 14px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  background: var(--color-bg-card);
}

.nested-tool > span, .nested-tool > strong {
  display: inline-block;
  margin: 9px 0 0 12px;
  color: var(--color-text-secondary);
  font-size: 10px;
}

.nested-tool > strong {
  margin-left: 6px;
  color: var(--color-text);
  font: 600 11px ui-monospace, monospace;
}

.nested-tool .item-fields {
  margin: 8px 10px 10px;
  background: var(--color-bg-secondary);
}

@media (max-width: 760px) {
  .item-fields > div {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
  .item-fields dd {
    grid-column: 1;
  }
}
</style>
