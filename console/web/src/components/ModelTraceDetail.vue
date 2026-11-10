<script setup lang="ts">
import { computed } from 'vue'
import TraceItemTimeline from './TraceItemTimeline.vue'
import { traceAttemptLabel, traceDuration, traceJson, traceReasoning, traceRecords, traceStage, type TraceRecord } from '@/utils/modelTrace'

const props = defineProps<{ snapshot: TraceRecord }>()
const metadata = computed(() => (props.snapshot.metadata || {}) as TraceRecord)
const outputs = computed(() => traceRecords(props.snapshot.output_items).filter((item) => item.type !== 'reasoning'))
const reasoning = computed(() => traceReasoning(props.snapshot))
const requests = computed(() => traceRecords(props.snapshot.request_items))
const attempts = computed(() => traceRecords(props.snapshot.generation_attempts))
const tools = computed(() => traceRecords(props.snapshot.tool_definitions))
const failed = computed(() => attempts.value[attempts.value.length - 1]?.status === 'failed')
</script>

<template>
  <div class="trace-reader">
    <header class="reader-heading">
      <div class="reader-eyebrow"><span>{{ traceStage(metadata.task) }}</span><span class="reader-status" :class="{ failed }">{{ failed ? '调用失败' : '调用记录' }}</span></div>
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
            <details class="attempt-request"><summary>完整调用数据</summary><pre class="reader-code">{{ traceJson(attempt) }}</pre></details>
          </div>
        </details>
      </div>
    </details>

    <details v-if="tools.length" class="reader-disclosure">
      <summary><strong>可用工具</strong><span>{{ tools.length }} 个定义</span></summary>
      <pre class="reader-code">{{ traceJson(tools) }}</pre>
    </details>
    <details class="reader-disclosure">
      <summary><strong>原始 JSON</strong><span>排查与调试</span></summary>
      <pre class="reader-code">{{ traceJson(snapshot) }}</pre>
    </details>
  </div>
</template>

<style scoped>
.trace-reader {
  min-width: 0;
  padding: 26px;
}

.reader-heading {
  padding-bottom: 24px;
  border-bottom: 1px solid var(--color-border);
}

.reader-eyebrow, .reader-meta, .reader-identity {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  align-items: center;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.reader-eyebrow > span:first-child {
  color: var(--color-primary);
  font-weight: 600;
}

.reader-status {
  margin-left: auto;
  color: var(--color-success);
}

.reader-status.failed {
  color: var(--color-danger);
}

.reader-heading h3 {
  margin: 14px 0 12px;
  color: var(--color-text);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.03em;
  overflow-wrap: anywhere;
}

.reader-identity {
  margin-top: 12px;
  font-size: 10px;
  overflow-wrap: anywhere;
}

.reader-section {
  padding: 24px 0;
}

.reader-section h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.reader-section h4 span {
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-weight: 400;
}

.final-output {
  border-bottom: 2px solid var(--color-border);
}

.reader-disclosure {
  margin-top: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.reader-disclosure > summary {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 15px 16px;
  cursor: pointer;
  list-style: none;
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
  padding: 4px 16px 16px;
}

.attempt-step {
  position: relative;
  margin-left: 12px;
  padding: 0 0 18px 20px;
  border-left: 1px solid var(--color-border);
}

.attempt-step:last-child {
  padding-bottom: 0;
}

.attempt-step > summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  list-style: none;
  cursor: pointer;
}

.attempt-number {
  position: absolute;
  left: -11px;
  display: grid;
  place-items: center;
  width: 21px;
  height: 21px;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: var(--color-bg-secondary);
  color: var(--color-primary);
  font-size: 10px;
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
  margin-top: 12px;
}

.attempt-request > summary {
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 11px;
}

.reader-code {
  max-height: 500px;
  margin: 0 16px 16px;
  padding: 16px;
  overflow: auto;
  border-radius: 6px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font: 11px/1.85 ui-monospace, monospace;
  white-space: pre-wrap;
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
