import { useState } from 'react'
import { apiFetch, setToken } from '../api.js'

export default function LoginPage({ onLogin }) {
  const [tab, setTab] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  function switchTab(t) { setTab(t); setError('') }

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      if (tab === 'register') {
        const r = await apiFetch('POST', '/api/auth/register', { username: username.trim(), password })
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail) }
      }
      const r = await apiFetch('POST', '/api/auth/login', { username: username.trim(), password })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail) }
      const d = await r.json()
      setToken(d.token)
      onLogin({ username: d.username, role: d.role })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Wikimania</h1>
        <p className="login-subtitle">Knowledge at your fingertips</p>
        <div className="login-tabs">
          <button className={`tab${tab === 'login' ? ' active' : ''}`} onClick={() => switchTab('login')}>Sign In</button>
          <button className={`tab${tab === 'register' ? ' active' : ''}`} onClick={() => switchTab('register')}>Register</button>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label>Username</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              required
              autoComplete="username"
            />
          </div>
          <div className="login-field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
            />
          </div>
          {error && <div className="login-error">{error}</div>}
          <button className="btn" type="submit" disabled={loading} style={{ width: '100%', marginTop: '0.25rem' }}>
            {loading
              ? <><span className="spinner" />{tab === 'login' ? 'Signing in...' : 'Creating account...'}</>
              : tab === 'login' ? 'Sign In' : 'Create Account'
            }
          </button>
        </form>
      </div>
    </div>
  )
}
