// 验证真实页面脚本的异步隔离与输入行为，不连接后台模型。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import * as vue from 'vue'

const source = readFileSync(new URL('../src/views/LocalChatView.vue', import.meta.url), 'utf8')
  .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function snapshot(id, content = '') {
  return { conversation: id, items: content ? [{ id, role: 'assistant', content }] : [],
    running: false, message_id: null, persona: '', provider: '', effective_persona: 'FxBot', can_chat: true }
}

function page(api = {}, confirm = async () => {}) {
  let unmount
  const context = vm.createContext({
    exports: {}, console, crypto: webcrypto, AbortController, setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem() {} },
    require(name) {
      if (name === 'vue') return { ...vue, onMounted() {}, onUnmounted(fn) { unmount = fn } }
      if (name === 'element-plus') return { ElMessage: { error() {} }, ElMessageBox: { confirm } }
      if (name === '@/api/localChat') return { localChatApi: { history: async (id) => snapshot(id), ...api } }
      return {}
    },
  })
  vm.runInContext(compiled + '\nexports.state = { active, draft, messages, busy, canChat, channelError, send, selectConversation, clearConversation, onKeydown, refreshHistory };', context)
  return { ...context.exports.state, unmount: () => unmount() }
}

test('保留本轮可见回复，即使短期历史设置为零', async () => {
  const state = page({ send: async (id, data, receive) => {
    receive({ type: 'accepted', id: data.message_id, content: data.text })
    receive({ type: 'message', id: 'reply', content: '你好' })
  } })
  state.active.value = A
  state.draft.value = '问候'
  await state.send()
  assert.equal(state.messages.value.length, 2)
  assert.equal(state.messages.value[1].content, '你好')
  state.unmount()
})

test('清空后，旧流中的迟到回复不能重新出现', async () => {
  const pending = deferred()
  let receive
  const state = page({
    send: async (id, data, callback) => { receive = callback; await pending.promise },
    clear: async () => ({ history_cleared: true }),
  })
  state.active.value = A
  state.draft.value = '消息'
  const sending = state.send()
  await state.clearConversation()
  receive({ type: 'message', id: 'stale', content: '过期正文' })
  pending.resolve()
  await sending
  assert.equal(state.messages.value.length, 0)
  state.unmount()
})

test('旧会话的历史响应不能覆盖新会话', async () => {
  const pending = deferred()
  const state = page({ history: (id) => id === A ? pending.promise : Promise.resolve(snapshot(id, 'B 正文')) })
  const first = state.selectConversation(A)
  await state.selectConversation(B)
  pending.resolve(snapshot(A, 'A 正文'))
  await first
  assert.equal(state.active.value, B)
  assert.equal(state.messages.value[0].content, 'B 正文')
  state.unmount()
})

test('确认清空期间切换会话，不能清空新会话', async () => {
  const pending = deferred()
  let clears = 0
  const state = page({ clear: async () => { clears++; return { history_cleared: true } } }, () => pending.promise)
  state.active.value = A
  const clearing = state.clearConversation()
  state.active.value = B
  pending.resolve()
  await clearing
  assert.equal(clears, 0)
  state.unmount()
})

test('中文输入法确认键与 Shift+Enter 不触发发送', async () => {
  let sent = 0
  const state = page({ send: async () => { sent++ } })
  state.active.value = A
  state.draft.value = '中文'
  for (const overrides of [{ isComposing: true }, { keyCode: 229 }, { shiftKey: true }]) {
    state.onKeydown({ key: 'Enter', shiftKey: false, isComposing: false, keyCode: 13, preventDefault() {}, ...overrides })
  }
  await Promise.resolve()
  assert.equal(sent, 0)
  state.unmount()
})

test('页面卸载会中止响应流，迟到数据不能更新页面', async () => {
  const pending = deferred()
  let signal, receive
  const state = page({ send: async (id, data, callback, abort) => {
    signal = abort; receive = callback; await pending.promise
  } })
  state.active.value = A
  state.draft.value = '消息'
  const sending = state.send()
  state.unmount()
  assert.equal(signal.aborted, true)
  receive({ type: 'message', content: '迟到' })
  pending.resolve()
  await sending
  assert.equal(state.messages.value.length, 0)
})
