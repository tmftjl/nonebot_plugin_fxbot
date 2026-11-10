// 直接执行页面脚本，验证异步响应不会覆盖另一个会话。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import * as vue from 'vue'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as ElementPlus from 'element-plus'
import * as icons from '@element-plus/icons-vue'

const source = readFileSync(new URL('../src/views/ChatReplyView.vue', import.meta.url), 'utf8')
  .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function settings(tenant) {
  return {
    tenant,
    persona: { bound: `${tenant}-persona`, effective: `${tenant}-persona`, options: [] },
    provider: { bound: '', effective: '', options: [] },
    talk_value: { bound: null, effective: 1 },
    proactive: { switch: null },
  }
}

function page(api, confirm = async () => {}) {
  const context = vm.createContext({
    exports: {}, console,
    localStorage: { getItem: () => null, setItem() {} },
    require(name) {
      if (name === 'vue') return { ...vue, onMounted() {} }
      if (name === 'element-plus') return {
        ElMessage: { success() {}, error() {} }, ElMessageBox: { confirm },
      }
      if (name === '@/api') return { chatApi: api }
      return {}
    },
  })
  vm.runInContext(compiled + `\nexports.state = {
    tenant, sessionSettings, sessionForm, modelTraces, snapshotDetail, settingsOpen, sessionDirty,
    applySessionSettings, loadTenants, loadSessionSettings, saveSessionSettings, resetSessionSettings, loadModelTraces,
  };`, context)
  return context.exports.state
}

test('保存 A 期间切换到 B，A 的响应不能覆盖 B 的表单', async () => {
  const saving = deferred()
  const state = page({ saveSession: () => saving.promise, getSession: async (tenant) => settings(tenant) })
  state.tenant.value = 'A'
  state.applySessionSettings(settings('A'))
  const pending = state.saveSessionSettings()
  state.tenant.value = 'B'
  await state.loadSessionSettings()
  saving.resolve({ settings: settings('A'), history_cleared: false })
  await pending
  assert.equal(state.sessionSettings.value.tenant, 'B')
  assert.equal(state.sessionForm.value.persona, 'B-persona')
})

test('恢复确认期间切换会话，不得重置新会话', async () => {
  const confirmation = deferred()
  let saves = 0
  const state = page({ saveSession: async () => { saves++ } }, () => confirmation.promise)
  state.tenant.value = 'A'
  const pending = state.resetSessionSettings()
  state.tenant.value = 'B'
  confirmation.resolve()
  await pending
  assert.equal(saves, 0)
})

test('旧会话的快照响应不能覆盖当前会话', async () => {
  const loading = deferred()
  const state = page({ listModelTraces: (tenant) => tenant === 'A' ? loading.promise : Promise.resolve({ items: [] }) })
  state.tenant.value = 'A'
  const pending = state.loadModelTraces()
  state.tenant.value = 'B'
  await state.loadModelTraces()
  loading.resolve({ items: [{ path: 'A-only' }] })
  await pending
  assert.equal(state.modelTraces.value.length, 0)
})

test('清空后刷新租户列表，不得自动切换到另一个会话', async () => {
  const state = page({ tenants: async () => ({ tenants: [{ tenant: 'B' }] }) })
  state.tenant.value = 'A'
  await state.loadTenants()
  assert.equal(state.tenant.value, 'A')
})

test('设置默认收起，展开不会丢失已加载的设置', () => {
  const state = page({})
  state.applySessionSettings(settings('A'))
  assert.equal(state.settingsOpen.value, false)
  assert.equal(state.sessionDirty.value, false)
  state.settingsOpen.value = true
  assert.equal(state.sessionForm.value.persona, 'A-persona')
})

test('设置修改提示可正确识别人格、提供方和回复频率变更', () => {
  const state = page({})
  const saved = settings('A')
  state.applySessionSettings(saved)
  state.sessionForm.value.persona = 'another-persona'
  assert.equal(state.sessionDirty.value, true)
  state.applySessionSettings(saved)
  state.sessionForm.value.provider = 'another-provider'
  assert.equal(state.sessionDirty.value, true)
  state.applySessionSettings(saved)
  state.sessionForm.value.talkValue = 25
  assert.equal(state.sessionDirty.value, false)
  state.sessionForm.value.talkOverride = true
  assert.equal(state.sessionDirty.value, true)
  state.applySessionSettings(saved)
  assert.equal(state.sessionDirty.value, false)
})

test('工作台模板可渲染，默认收起设置并保留对话和详情区域', async () => {
  const sfc = readFileSync(new URL('../src/views/ChatReplyView.vue', import.meta.url), 'utf8')
  const populated = sfc.replace('onMounted(() => {', `
    tenant.value = 'test:bot:group:123'
    loaded.value = true
    applySessionSettings(${JSON.stringify(settings('test:bot:group:123'))})
    recentMessages.value = [{ role: 'assistant', content: '测试回复', timestamp: null, sender_name: '', sender_id: '', at_bot: false, quote: '' }]
    onMounted(() => {`)
  const { descriptor } = parse(populated)
  const component = compileScript(descriptor, { id: 'reply-view-test', inlineTemplate: true })
  const code = ts.transpileModule(component.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const child = vue.defineComponent({
    inheritAttrs: false,
    props: ['title', 'subtitle', 'modelValue', 'label', 'value', 'hint', 'icon', 'tone'],
    setup: (props, { slots }) => () => vue.h(
      'div',
      { class: props.label ? 'stub-metric' : undefined },
      [props.label, props.value, props.title, props.subtitle, slots.default?.()],
    ),
  })
  const context = vm.createContext({
    exports: {}, console,
    localStorage: { getItem: () => null, setItem() {} },
    require(name) {
      if (name === 'vue') return vue
      if (name === 'element-plus') return ElementPlus
      if (name === '@element-plus/icons-vue') return icons
      if (name === '@/api') return { chatApi: {} }
      return { default: child }
    },
  })
  vm.runInContext(code, context)
  const app = vue.createSSRApp(context.exports.default)
  app.use(ElementPlus.default)
  app.provide(ElementPlus.ID_INJECTION_KEY, { prefix: 1024, current: 0 })
  app.provide(ElementPlus.ZINDEX_INJECTION_KEY, { current: 0 })
  const html = await renderToString(app)
  assert.match(html, /id="session-settings"[^>]*display:none/)
  assert.match(html, /aria-expanded="false"/)
  assert.match(html, /测试回复/)
  assert.match(html, /class="overview-card facts-card"/)
  assert.equal((html.match(/class="facts-column"/g) || []).length, 2)
  assert.equal((html.match(/class="stub-metric"/g) || []).length, 4)
  assert.doesNotMatch(html, /本会话配置/)
})
