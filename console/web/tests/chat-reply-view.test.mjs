// 直接执行页面脚本，验证异步响应不会覆盖另一个会话。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import * as vue from 'vue'

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
    tenant, sessionSettings, sessionForm, modelTraces, snapshotDetail, snapshotReasoning,
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

test('重生成无思考文字时仍能查看首次推理', () => {
  const state = page({})
  state.snapshotDetail.value = {
    output_items: [{ type: 'message', text: '正文' }],
    generation_attempts: [
      { output_items: [{ type: 'reasoning', text: '首次思考' }] },
      { output_items: [{ type: 'message', text: '正文' }] },
    ],
  }
  assert.equal(state.snapshotReasoning.value[0].text, '首次思考')
})

test('清空后刷新租户列表，不得自动切换到另一个会话', async () => {
  const state = page({ tenants: async () => ({ tenants: [{ tenant: 'B' }] }) })
  state.tenant.value = 'A'
  await state.loadTenants()
  assert.equal(state.tenant.value, 'A')
})
