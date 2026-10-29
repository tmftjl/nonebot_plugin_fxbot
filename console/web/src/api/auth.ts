// -*- coding: utf-8 -*-
import { reactive } from 'vue'

const TOKEN_KEY = 'fxbot_console_token'

/** 首屏读取 Token：链接里的 ?token= 优先，读完从地址栏抹掉。 */
const readToken = (): string => {
  try {
    const params = new URLSearchParams(window.location.search)
    const fromUrl = params.get('token')
    if (fromUrl) {
      localStorage.setItem(TOKEN_KEY, fromUrl)
      params.delete('token')
      const query = params.toString()
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
      )
      return fromUrl
    }
  } catch {}
  return localStorage.getItem(TOKEN_KEY) || ''
}

export const authState = reactive({
  token: readToken(),
  username: '',
  mustChange: false,
  message: '',
})

interface Session {
  token: string
  username: string
  must_change_password: boolean
}

export function getToken(): string {
  return authState.token
}

export function setToken(token: string): void {
  authState.token = token
  authState.message = ''
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(message = ''): void {
  authState.token = ''
  authState.username = ''
  authState.mustChange = false
  authState.message = message
  localStorage.removeItem(TOKEN_KEY)
}

const applySession = (session: Session) => {
  setToken(String(session.token || ''))
  authState.username = String(session.username || '')
  authState.mustChange = Boolean(session.must_change_password)
}

const errorDetail = async (response: Response, fallback: string): Promise<string> => {
  try {
    const data = await response.json()
    return String(data.detail || fallback)
  } catch {
    return fallback
  }
}

/** 用户名密码登录，成功后保存访问 Token。 */
export async function login(username: string, password: string): Promise<void> {
  let response: Response
  try {
    response = await fetch('/fxbot/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  } catch {
    throw new Error('无法连接控制台服务')
  }
  if (response.status === 401) throw new Error('用户名或密码不正确')
  if (!response.ok) throw new Error(await errorDetail(response, `控制台返回 ${response.status}`))
  applySession((await response.json()) as Session)
}

/** 拉取当前账户信息；Token 失效时清掉凭证。 */
export async function loadProfile(): Promise<boolean> {
  if (!authState.token) return false
  try {
    const response = await fetch('/fxbot/auth/me', {
      headers: { Authorization: `Bearer ${authState.token}` },
    })
    if (response.status === 401 || response.status === 403) {
      clearToken('登录已过期，请重新登录')
      return false
    }
    if (!response.ok) return true
    const data = await response.json()
    authState.username = String(data.username || '')
    authState.mustChange = Boolean(data.must_change_password)
    return true
  } catch {
    return true
  }
}

/** 修改密码；服务端会同时换发访问 Token。 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  let response: Response
  try {
    response = await fetch('/fxbot/auth/password', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authState.token}`,
      },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    })
  } catch {
    throw new Error('无法连接控制台服务')
  }
  if (response.status === 401 || response.status === 403) {
    clearToken('登录已过期，请重新登录')
    throw new Error('登录已过期，请重新登录')
  }
  if (!response.ok) throw new Error(await errorDetail(response, '修改密码失败'))
  applySession((await response.json()) as Session)
}
