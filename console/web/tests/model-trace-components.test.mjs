import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import * as icons from '@element-plus/icons-vue'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import ts from 'typescript'

function evaluate(source, require) {
  const context = vm.createContext({ exports: {}, require, console })
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  vm.runInContext(code, context)
  return context.exports
}

const utils = evaluate(readFileSync(new URL('../src/utils/modelTrace.ts', import.meta.url), 'utf8'))
function component(name, api = {}) {
  let source = readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  if (name === 'ModelTraceBrowser') {
    source = source.replace('</script>', 'defineExpose({ query, stage, status, page, selectedPath, detail, detailLoading, detailError })\n</script>')
  }
  const { descriptor } = parse(source)
  return evaluate(compileScript(descriptor, { id: `test-${name}`, inlineTemplate: true }).content, (module) => {
    if (module === 'vue') return vue
    if (module === '@/api') return { chatApi: api }
    if (module === '@/utils/modelTrace') return utils
    if (module === '@element-plus/icons-vue') return icons
    if (module.endsWith('.vue')) return { default: component(module.split('/').pop().replace('.vue', ''), api) }
    throw new Error(`Unexpected import: ${module}`)
  }).default
}

// 在内存渲染树中验证交互，不连接浏览器或真实后端。
function mountBrowser(api, initial) {
  const renderer = vue.createRenderer({
    createElement: (type) => ({ type, children: [], props: {}, parent: null }),
    createText: (text) => ({ text, children: [], parent: null }),
    createComment: () => ({ children: [], parent: null }),
    setText: (node, text) => { node.text = text },
    setElementText: (node, text) => { node.text = text; node.children = [] },
    patchProp: (node, key, previous, next) => { node.props[key] = next },
    parentNode: (node) => node.parent,
    nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
    insert(node, parent, anchor = null) {
      if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
      node.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(node)
      else parent.children.splice(index, 0, node)
    },
    remove(node) {
      if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
      node.parent = null
    },
  })
  const props = vue.reactive(initial)
  const instance = vue.ref(null)
  const Browser = component('ModelTraceBrowser', api)
  const app = renderer.createApp({ render: () => vue.h(Browser, { ...props, ref: instance }) })
  const stub = vue.defineComponent({ setup: (_, { slots }) => () => vue.h('div', slots.default?.()) })
  for (const name of ['ElInput', 'ElSelect', 'ElOption', 'ElButton', 'ElIcon']) app.component(name, stub)
  app.mount({ children: [] })
  return { props, instance, app }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const row = (path, task = 'reply') => ({
  path, task, category: task, status: 'completed', model_name: 'model', created_at: '2026-11-10T10:00:00+08:00',
  size: 100, duration_ms: 10, reply_preview: path, reasoning_preview: '',
})
const snapshot = (label) => ({ metadata: { model_name: label }, output_items: [{ type: 'message', text: label }] })
async function flush() { await vue.nextTick(); await Promise.resolve(); await vue.nextTick() }

test('切换会话时，旧详情不能覆盖新会话的思考记录', async () => {
  const a = deferred()
  const b = deferred()
  const browser = mountBrowser({ getModelTrace: (path) => path === 'a' ? a.promise : b.promise }, {
    tenant: 'session-a', items: [row('a')], loading: false,
  })
  try {
    browser.props.tenant = 'session-b'
    browser.props.items = [row('b')]
    await flush()
    a.resolve({ snapshot: snapshot('old') })
    await flush()
    assert.equal(browser.instance.value.detail, null)
    b.resolve({ snapshot: snapshot('new') })
    await flush()
    assert.equal(browser.instance.value.detail.metadata.model_name, 'new')
  } finally { browser.app.unmount() }
})

test('自动选中首条记录，筛选与清空后详情保持一致', async () => {
  const requested = []
  const browser = mountBrowser({ getModelTrace: async (path) => {
    requested.push(path)
    return { snapshot: snapshot(path) }
  } }, { tenant: 'session', items: [row('reply'), row('planner', 'planner')], loading: false })
  try {
    await flush()
    assert.equal(browser.instance.value.detail.metadata.model_name, 'reply')
    browser.instance.value.stage = 'planner'
    await flush()
    assert.equal(browser.instance.value.detail.metadata.model_name, 'planner')
    browser.instance.value.query = 'no-match'
    await flush()
    assert.equal(browser.instance.value.detail, null)
    assert.equal(browser.instance.value.detailLoading, false)
    assert.deepEqual(requested, ['reply', 'planner'])
  } finally { browser.app.unmount() }
})

test('详情输出优先，完整思考按正文展示，工具和原始数据可展开', async () => {
  const Detail = component('ModelTraceDetail')
  const html = await renderToString(vue.createSSRApp(Detail, { snapshot: {
    metadata: { model_name: 'test-model', task: 'reply', duration_ms: 1200 },
    output_items: [{ type: 'message', text: '最终回复 <script>alert(1)</script>' }],
    request_items: [{ role: 'system', content: '系统上下文' }],
    generation_attempts: [{
      workflow_purpose: 'replyer.reasoning_initial', status: 'completed',
      output_items: [{ type: 'reasoning', text: '这是完整推理内容' }],
    }],
  } }))
  assert.ok(html.indexOf('最终回复') < html.indexOf('这是完整推理内容'))
  assert.match(html, /系统上下文/)
  assert.match(html, /首次推理/)
  assert.match(html, /生成调用链/)
  assert.match(html, /原始 JSON/)
  assert.doesNotMatch(html, /<script>alert/)
  assert.match(html, /&lt;script&gt;alert/)
  assert.doesNotMatch(html, /<table/)
})
