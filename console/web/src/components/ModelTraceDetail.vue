<script setup lang="ts">
import { computed } from 'vue'
import TraceItemTimeline from './TraceItemTimeline.vue'
import { traceAttemptLabel, traceDuration, traceReasoning, traceRecords, traceStage, type TraceRecord } from '@/utils/modelTrace'

const props = defineProps<{ snapshot: TraceRecord }>()
const metadata = computed(() => (props.snapshot.metadata || {}) as TraceRecord)
const outputs = computed(() => traceRecords(props.snapshot.output_items).filter((item) => item.type !== 'reasoning'))
const reasoning = computed(() => traceReasoning(props.snapshot))
const requests = computed(() => traceRecords(props.snapshot.request_items))
const attempts = computed(() => traceRecords(props.snapshot.generation_attempts))
const tools = computed(() => traceRecords(props.snapshot.tool_definitions).map((tool) => {
  const fn = tool.function && typeof tool.function === 'object' ? tool.function as TraceRecord : tool
  return { name: String(fn.name || '未命名工具'), description: String(fn.description || '') }
}))
const failed = computed(() => attempts.value[attempts.value.length - 1]?.status === 'failed')
</script>

<template>
  <div class="trace-reader">
    <header class="reader-heading">
      <div class="reader-eyebrow"><span class="stage-chip">{{ traceStage(metadata.task) }}</span><span class="reader-status" :class="{ failed }">{{ failed ? '调用失败' : '调用完成' }}</span></div>
      <h3>{{ metadata.model_name || '未记录模型名称' }}</h3>
      <div class="reader-meta"><span>{{ metadata.provider || '未知提供方' }}</span><span>{{ traceDuration(metadata.duration_ms) }}</span><span v-if="metadata.total_tokens !== undefined">{{ metadata.total_tokens }} tokens</span></div>
      <div class="reader-identity"><span>轮次 {{ metadata.turn_id || '未绑定' }}</span><span>{{ metadata.session_id || '未绑定会话' }}</span></div>
    </header>

    <section class="reader-section final-output">
      <h4>模型输出 <span>{{ outputs.length }}</span></h4>
      <TraceItemTimeline :items="outputs" empty-text="本次没有正文或工具输出，请查看下方思考过程与调用链。" />
    </section>

    <section v-if="reasoning.length" class="reader-section">
      <h4>思考过程 <span>{{ reasoning.length }}</span></h4>
      <TraceItemTimeline :items="reasoning" />
    </section>

    <details class="reader-disclosure">
      <summary><strong>请求上下文</strong><span>{{ requests.length }} 条消息</span></summary>
      <div class="disclosure-body"><TraceItemTimeline :items="requests" /></div>
    </details>

    <details v-if="attempts.length" class="reader-disclosure" :open="attempts.length > 1 || failed">
      <summary><strong>生成调用链</strong><span>{{ attempts.length }} 次调用</span></summary>
      <div class="attempt-timeline">
        <details v-for="(attempt, index) in attempts" :key="index" class="attempt-step" :class="{ failed: attempt.status === 'failed' }" :open="attempt.status === 'failed'">
          <summary>
            <span class="attempt-number">{{ index + 1 }}</span>
            <div><strong>{{ traceAttemptLabel(attempt, index) }}</strong><small>{{ attempt.model || '未知模型' }} · {{ traceDuration(attempt.duration_ms) }}</small></div>
            <span class="attempt-status">{{ attempt.status === 'failed' ? '失败' : '完成' }}</span>
          </summary>
          <div class="attempt-body">
            <p v-if="attempt.error" class="attempt-error">{{ attempt.error }}</p>
            <TraceItemTimeline :items="traceRecords(attempt.output_items)" />
            <details class="attempt-request"><summary>本次请求 · {{ traceRecords(attempt.request_items).length }} 条</summary><TraceItemTimeline :items="traceRecords(attempt.request_items)" /></details>
          </div>
        </details>
      </div>
    </details>

    <details v-if="tools.length" class="reader-disclosure">
      <summary><strong>可用工具</strong><span>{{ tools.length }} 个定义</span></summary>
      <ul class="tool-list">
        <li v-for="(tool, index) in tools" :key="index">
          <strong>{{ tool.name }}</strong>
          <p v-if="tool.description">{{ tool.description }}</p>
        </li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.trace-reader {
  min-width: 0;
  padding: 24px 26px;
}

.reader-heading {
  padding-bottom: 20px;
  border-bottom: 1px solid var(--color-border-light);
}

.reader-eyebrow, .reader-meta, .reader-identity {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.stage-chip {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.reader-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  color: var(--color-success);
  font-size: 10px;
}

.reader-status::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.reader-status.failed {
  color: var(--color-danger);
}

.reader-heading h3 {
  margin: 12px 0 10px;
  color: var(--color-text);
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -0.03em;
  overflow-wrap: anywhere;
}

.reader-meta {
  gap: 0 10px;
}

.reader-meta > span + span::before {
  content: '·';
  margin-right: 10px;
  color: var(--color-text-tertiary);
}

.reader-identity {
  gap: 8px 16px;
  margin-top: 12px;
  font-size: 10px;
  overflow-wrap: anywhere;
}

.reader-section {
  padding: 22px 0 20px;
}

.reader-section h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
}

.reader-section h4::before {
  content: '';
  width: 3px;
  height: 13px;
  border-radius: 2px;
  background: var(--color-primary);
}

.reader-section h4 span {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-weight: 400;
}

.final-output {
  border-bottom: 1px solid var(--color-border-light);
}

.reader-disclosure {
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: 10px;
  background: var(--color-bg-secondary);
}

.reader-disclosure > summary {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 13px 16px;
  cursor: pointer;
  list-style: none;
}

.reader-disclosure[open] > summary {
  border-bottom: 1px solid var(--color-border-light);
}

.reader-disclosure > summary::before {
  content: '+';
  color: var(--color-text-tertiary);
  width: 12px;
}

.reader-disclosure[open] > summary::before {
  content: '−';
}

.reader-disclosure summary strong {
  font-size: 12px;
  font-weight: 550;
  color: var(--color-text);
}

.reader-disclosure > summary > span {
  margin-left: auto;
  font-size: 10px;
  color: var(--color-text-tertiary);
}

.disclosure-body, .attempt-timeline {
  padding: 14px 16px 16px;
}

.attempt-step {
  position: relative;
  margin-left: 10px;
  padding: 0 0 16px 18px;
  border-left: 1px dashed var(--color-border);
}

.attempt-step:last-child {
  padding-bottom: 0;
}

.attempt-step > summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  list-style: none;
  cursor: pointer;
}

.attempt-number {
  position: absolute;
  left: -10px;
  display: grid;
  place-items: center;
  width: 19px;
  height: 19px;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: var(--color-bg-secondary);
  color: var(--color-primary);
  font-size: 10px;
  font-weight: 600;
}

.attempt-step small {
  display: block;
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.attempt-status {
  margin-left: auto;
  color: var(--color-success);
  font-size: 10px;
}

.attempt-step.failed .attempt-status, .attempt-error {
  color: var(--color-danger);
}

.attempt-error {
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.attempt-body {
  padding: 10px 0;
}

.attempt-request {
  margin-top: 10px;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
}

.attempt-request > summary {
  padding: 9px 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 11px;
  list-style: none;
}

.attempt-request > summary::before {
  content: '+';
  margin-right: 8px;
  color: var(--color-text-tertiary);
}

.attempt-request[open] > summary::before {
  content: '−';
}

.attempt-request[open] > summary {
  border-bottom: 1px solid var(--color-border-light);
}

.attempt-request > .item-timeline {
  padding: 12px;
}

.tool-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 12px 16px 16px;
  list-style: none;
}

.tool-list li {
  padding: 10px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  background: var(--color-bg-card);
}

.tool-list strong {
  color: var(--color-text);
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

.tool-list p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

@media (max-width: 760px) {
  .trace-reader {
    padding: 18px;
  }
  .reader-heading h3 {
    font-size: 17px;
  }
}
</style>
