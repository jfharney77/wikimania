const BASE = import.meta.env.VITE_API_URL ?? ''

export function getToken() {
  return localStorage.getItem('wikimania_token')
}

export function setToken(t) {
  if (t) localStorage.setItem('wikimania_token', t)
  else localStorage.removeItem('wikimania_token')
}

export async function apiFetch(method, path, body = null) {
  const token = getToken()
  const isFormData = body instanceof FormData
  const headers = {}
  if (!isFormData && body !== null) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: isFormData ? body : (body !== null ? JSON.stringify(body) : null),
  })
  if (res.status === 401) {
    setToken(null)
    window.dispatchEvent(new Event('auth:logout'))
  }
  return res
}

// EventSource doesn't support custom headers — pass token as query param for SSE only.
export function streamUrl(path) {
  const token = getToken()
  if (!token) return `${BASE}${path}`
  const sep = path.includes('?') ? '&' : '?'
  return `${BASE}${path}${sep}token=${encodeURIComponent(token)}`
}
