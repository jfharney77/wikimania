import { useState, useEffect } from 'react'
import { apiFetch } from '../api.js'

export default function AdminPanel({ currentUser }) {
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => { fetchUsers() }, [])

  async function fetchUsers() {
    try {
      const r = await apiFetch('GET', '/api/admin/users')
      if (r.ok) { const d = await r.json(); setUsers(d.users) }
    } catch { /* ignore */ }
  }

  async function handleCreate(e) {
    e.preventDefault()
    setError(''); setSuccess('')
    setLoading(true)
    try {
      const r = await apiFetch('POST', '/api/admin/users', { ...form, username: form.username.trim() })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail) }
      setSuccess(`User "${form.username.trim()}" created.`)
      setForm({ username: '', password: '', role: 'user' })
      fetchUsers()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleRoleChange(user, newRole) {
    const r = await apiFetch('PATCH', `/api/admin/users/${user.id}/role`, { role: newRole })
    if (r.ok) fetchUsers()
  }

  async function handleDelete(user) {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return
    const r = await apiFetch('DELETE', `/api/admin/users/${user.id}`)
    if (r.ok) fetchUsers()
  }

  return (
    <div className="admin-panel">
      <h2>User Management</h2>

      <section className="admin-section">
        <h3>Create User</h3>
        <form onSubmit={handleCreate}>
          <div className="admin-form-row">
            <div className="login-field">
              <label>Username</label>
              <input
                value={form.username}
                onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                placeholder="username"
                required
              />
            </div>
            <div className="login-field">
              <label>Password</label>
              <input
                type="password"
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                placeholder="min 6 chars"
                required
              />
            </div>
            <div className="login-field">
              <label>Role</label>
              <select
                value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                className="role-select"
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <button className="btn" type="submit" disabled={loading} style={{ alignSelf: 'flex-end' }}>
              {loading ? <><span className="spinner" />Creating...</> : 'Create'}
            </button>
          </div>
          {error && <div className="login-error" style={{ marginTop: '0.5rem' }}>{error}</div>}
          {success && <div className="admin-success">{success}</div>}
        </form>
      </section>

      <section className="admin-section">
        <h3>All Users <span className="user-count">({users.length})</span></h3>
        <table className="user-table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id}>
                <td>
                  {u.username}
                  {u.username === currentUser.username && <span className="you-badge">you</span>}
                </td>
                <td>
                  {u.username === currentUser.username ? (
                    <span className={`role-badge role-${u.role}`}>{u.role}</span>
                  ) : (
                    <select
                      value={u.role}
                      onChange={e => handleRoleChange(u, e.target.value)}
                      className="role-select"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  )}
                </td>
                <td className="user-date">{new Date(u.created_at).toLocaleDateString()}</td>
                <td>
                  {u.username !== currentUser.username && (
                    <button className="btn-danger-outline" onClick={() => handleDelete(u)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
