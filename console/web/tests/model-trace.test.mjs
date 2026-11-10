// 模型记录的展示规则只依赖结构化数据，不解析或执行模型返回的 HTML。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const code = ts.transpileModule(
  readFileSync(new URL('../src/utils/modelTrace.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText
const context = vm.createContext({ exports: {} })
vm.runInContext(code, context)
const { filterModelTraces, traceReasoning, traceItemText, traceItemLabel, traceStage, traceDuration } = context.exports

test('按阶段、状态与摘要筛选思考记录', () => {
  const items = [
    { path: 'a', task: 'reply', status: 'completed', model_name: 'model-a', reply_preview: '你好世界' },
    { path: 'b', task: 'planner', status: 'failed', provider: 'provider-b', reasoning_preview: '先检查工具' },
  ]
  assert.equal(filterModelTraces(items, '世界', '', '')[0].path, 'a')
  assert.equal(filterModelTraces(items, '规划器', 'planner', 'failed')[0].path, 'b')
  assert.equal(filterModelTraces(items, 'MODEL-A', '', '')[0].path, 'a')
  assert.equal(filterModelTraces(items, '', 'reply', 'failed').length, 0)
})

test('重生成和续写按尝试保留首次思考，不重复顶层输出', () => {
  const reasoning = traceReasoning({
    output_items: [{ type: 'reasoning', text: '重复内容' }],
    generation_attempts: [
      { workflow_purpose: 'replyer.reasoning_initial', output_items: [{ type: 'reasoning', text: '首次思考' }] },
      { workflow_purpose: 'replyer.reasoning_continuation', output_items: [{ type: 'reasoning', text: '接着想' }] },
      { output_items: [{ type: 'message', text: '正文' }] },
    ],
  })
  assert.equal(reasoning.length, 2)
  assert.equal(reasoning[0].text, '首次思考')
  assert.equal(reasoning[0].attemptLabel, '首次推理')
  assert.equal(reasoning[1].attemptLabel, '接续推理')
})

test('没有尝试思考时读取顶层条目，加密推理不伪造文字', () => {
  assert.equal(traceReasoning({ output_items: [{ type: 'reasoning', text: '已有思考' }] })[0].text, '已有思考')
  const items = traceReasoning({ generation_attempts: [{
    output_items: [], provider_output_items: [{ type: 'reasoning', encrypted_content: 'secret', summary: [] }],
  }] })
  assert.equal(items.length, 1)
  assert.equal(traceItemText(items[0]), '')
})

test('原生内容块和工具结果转成可读内容，不把 HTML 当页面执行', () => {
  assert.equal(traceItemText({ type: 'reasoning', summary: [{ text: '推理摘要' }] }), '推理摘要')
  assert.equal(traceItemText({ role: 'assistant', content: [{ type: 'output_text', text: '<script>alert(1)</script>' }] }), '<script>alert(1)</script>')
  assert.match(traceItemText({ type: 'function_call_output', output: { ok: true } }), /"ok": true/)
  assert.equal(traceItemLabel({ role: 'system' }), '系统指令')
  assert.equal(traceItemLabel({ type: 'function_call' }), '工具调用')
  assert.equal(traceItemLabel({ type: 'function_call_output' }), '工具结果')
  assert.equal(traceStage('reply'), '回复器')
  assert.equal(traceDuration(1250), '1.25 s')
})
