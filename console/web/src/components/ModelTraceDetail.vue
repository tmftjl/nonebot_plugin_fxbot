<script setup lang="ts">
import { computed } from 'vue'
import TraceItemTimeline from './TraceItemTimeline.vue'
import { traceAttemptLabel, traceDuration, traceProcessing, traceRecords, traceSteps, type TraceRecord } from '@/utils/modelTrace'

const props = defineProps<{ snapshots: TraceRecord[] }>()

const steps = computed(() => traceSteps(props.snapshots))
const processing = computed(() => traceProcessing(steps.value))
const failed = computed(() => steps.value[steps.value.length - 1]?.status === 'failed')
const models = computed(() => [...new Set(steps.value.map((step) => step.model || step.provider).filter(Boolean))])
const providers = computed(() => [...new Set(steps.value.map((step) => step.provider).filter(Boolean))])
const duration = computed(() => steps.value.reduce((total, step) => total + step.duration, 0))
const tokens = computed(() => {
  const values = steps.value.map((step) => step.tokens).filter((value): value is number => value !== null)
  return values.length ? values.reduce((total, value) => total + value, 0) : null
})
const turnId = computed(() => steps.value.map((step) => String(step.metadata.turn_id || '')).find(Boolean) || '')
const sessionId = computed(() => steps.value.map((step) => String(step.metadata.session_id || '')).find(Boolean) || '')
const tools = computed(() => {
  const seen = new Set<string>()
  const items: Array<{ name: string; description: string }> = []
  for (const step of steps.value) {
    for (const tool of step.toolDefinitions) {
      const fn = tool.function && typeof tool.function === 'object' ? tool.function as TraceRecord : tool
      const name = String(fn.name || '')
      if (!name || seen.has(name)) continue
      seen.add(name)
      items.push({ name, description: String(fn.description || '') })
    }
  }
  return items
})
</script>

<template>
  <div class="trace-reader">
    <header class="reader-heading">
      <div class="reader-eyebrow">
        <span class="processing-chip">一次处理</span>
        <span class="processing-count">{{ steps.length }} 次调用</span>
        <span class="reader-status" :class="{ failed }">{{ failed ? '处理失败' : '处理完成' }}</span>
      </div>
      <h3>{{ models.join(' · ') || '未记录模型名称' }}</h3>
      <div class="reader-meta">
        <span>{{ providers.join(' / ') || '未知提供方' }}</span>
        <span>{{ traceDuration(duration) }}</span>
        <span v-if="tokens !== null">{{ tokens }} tokens</span>
      </div>
      <div class="reader-identity"><span>轮次 {{ turnId || '未绑定' }}</span><span>{{ sessionId || '未绑定会话' }}</span></div>
    </header>

    <section class="reader-section final-output">
      <h4>回复输出 <span>{{ processing.replies.length }}</span></h4>
      <TraceItemTimeline :items="processing.replies" empty-text="本次处理没有产生可见回复。" />
    </section>

    <section class="reader-section">
      <h4>处理过程 <span>{{ processing.timeline.length }}</span></h4>
      <TraceItemTimeline :items="processing.timeline" empty-text="没有记录到思考、工具调用或异常。" />
    </section>

    <details v-if="steps.length" class="reader-disclosure" :open="steps.length > 1 || failed">
      <summary><strong>调用明细</strong><span>{{ steps.length }} 次模型调用</span></summary>
      <div class="attempt-timeline">
        <details v-for="(step, index) in steps" :key="index" class="attempt-step" :class="{ failed: step.status === 'failed' }" :open="step.status === 'failed'">
          <summary>
            <span class="attempt-number">{{ index + 1 }}</span>
            <div>
              <strong>{{ step.model || '未知模型' }}</strong>
              <small>{{ step.provider || '未知提供方' }} · {{ traceDuration(step.duration) }}<template v-if="step.tokens !== null"> · {{ step.tokens }} tokens</template></small>
            </div>
            <span class="attempt-status">{{ step.status === 'failed' ? '失败' : '完成' }}</span>
          </summary>
          <div class="attempt-body">
            <p v-if="step.error" class="attempt-error">{{ step.error }}</p>
            <details v-for="(attempt, attemptIndex) in step.attempts" :key="attemptIndex" class="attempt-request">
              <summary>
                {{ step.attempts.length > 1 ? traceAttemptLabel(attempt, attemptIndex) : '请求上下文' }}
                · {{ traceRecords(attempt.request_items).length }} 条消息
              </summary>
              <TraceItemTimeline :items="traceRecords(attempt.request_items)" />
            </details>
            <details v-if="!step.attempts.length && step.requestItems.length" class="attempt-request">
              <summary>请求上下文 · {{ step.requestItems.length }} 条消息</summary>
              <TraceItemTimeline :items="step.requestItems" />
            </details>
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

.processing-chip {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.processing-count {
  margin-left: 8px;
  font-size: 10px;
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
