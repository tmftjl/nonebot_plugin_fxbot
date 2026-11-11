// 验证流式 API 使用 Bearer，并正确处理跨块中文和多行事件。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import axios from 'axios'
import ts from 'typescript'

const source = readFileSync(new URL('../src/api/index.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function client(fetch) {
  const context = vm.createContext({
    exports: {}, console, fetch, TextDecoder,
    require(name) {
      if (name === 'axios') return { default: axios }
      if (name === './auth') return { getToken: () => 'test-token', clearToken() {} }
      return {}
    },
  })
  vm.runInContext(compiled, context)
  return context.exports.api
}

test('跨 UTF-8 字节边界与多个 JSON 行仍能完整读取', async () => {
  const events = [{ type: 'message', content: '你好，网页' }, { type: 'done' }]
  const bytes = new TextEncoder().encode(events.map((event) => JSON.stringify(event)).join('\n') + '\n')
  let authorization
  const api = client(async (url, request) => {
    assert.equal(url, '/fxbot/local-chat/test/messages')
    authorization = request.headers.Authorization
    return new Response(new ReadableStream({
      start(controller) {
        for (let offset = 0; offset < bytes.length; offset += 2) controller.enqueue(bytes.slice(offset, offset + 2))
        controller.close()
      },
    }))
  })
  const seen = []
  await api.stream('/local-chat/test/messages', {}, (event) => seen.push(event), new AbortController().signal)
  assert.equal(authorization, 'Bearer test-token')
  assert.equal(JSON.stringify(seen), JSON.stringify(events))
})

test('回调失败会取消 reader，不遗留未消费的响应', async () => {
  let cancelled = false
  const api = client(async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"type":"message"}\n')) },
    cancel() { cancelled = true },
  })))
  await assert.rejects(api.stream('/local-chat/test/messages', {}, () => { throw new Error('test') }, new AbortController().signal))
  assert.equal(cancelled, true)
})
