import { Capacitor } from '@capacitor/core'
import { SecureStorage } from '@aparajita/capacitor-secure-storage'

const API_ORIGIN = __UPLINK_API_ORIGIN__
const TOKEN_KEY = 'session'
const originalFetch = window.fetch.bind(window)
const isNative = Capacitor.isNativePlatform()
let accessToken = ''

function apiUrl(input) {
  if (typeof input !== 'string' || !input.startsWith('/api/')) return input
  return API_ORIGIN + input
}

function apiPath(input) {
  if (typeof input !== 'string') return ''
  try { return new URL(input, location.href).pathname } catch { return '' }
}

const ready = (async () => {
  if (!isNative) return
  if (!/^https:\/\//.test(API_ORIGIN)) throw new Error('UPLINK_API_ORIGIN must use HTTPS')
  await SecureStorage.setKeyPrefix('uplink_')
  accessToken = await SecureStorage.getItem(TOKEN_KEY) || ''
})()

const nativeFetch = async (input, init = {}) => {
  await ready
  const path = apiPath(input)
  const headers = new Headers(init.headers || {})
  if (path.startsWith('/api/')) headers.set('X-Uplink-Client', 'native')
  if (accessToken && path.startsWith('/api/')) headers.set('Authorization', 'Bearer ' + accessToken)
  const response = await originalFetch(apiUrl(input), { ...init, headers, credentials: 'omit' })

  if (response.ok && (path === '/api/account/login' || path === '/api/account/register')) {
    const payload = await response.clone().json()
    if (payload.access_token) {
      accessToken = payload.access_token
      await SecureStorage.setItem(TOKEN_KEY, accessToken)
    }
  } else if (path === '/api/account/logout') {
    accessToken = ''
    await SecureStorage.removeItem(TOKEN_KEY)
  } else if (response.status === 401 && path === '/api/account/me') {
    accessToken = ''
    await SecureStorage.removeItem(TOKEN_KEY)
  }
  return response
}

async function openWebSocket(input) {
  await ready
  const requested = new URL(input, location.href)
  const ticketResponse = await window.fetch('/api/account/ws-ticket', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: requested.pathname }),
  })
  const payload = await ticketResponse.json()
  if (!ticketResponse.ok || !payload.ticket) throw new Error(payload.error || 'Unable to open voice channel')
  const target = new URL(API_ORIGIN)
  target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:'
  target.pathname = requested.pathname
  target.search = '?ticket=' + encodeURIComponent(payload.ticket)
  return new WebSocket(target.toString())
}

if (isNative) {
  window.fetch = nativeFetch
  window.UplinkNative = { apiOrigin: API_ORIGIN, isNative, openWebSocket, ready }
}
