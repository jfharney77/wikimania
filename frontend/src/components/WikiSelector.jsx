import { useState } from 'react'

export default function WikiSelector({ wikis, currentWiki, onSelect, onCreate, onDelete }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    await onCreate(name.trim())
    setName('')
    setCreating(false)
  }

  async function handleDelete() {
    await onDelete(currentWiki)
    setConfirmDelete(false)
  }

  return (
    <div className="wiki-selector">
      {!creating ? (
        <>
          <select
            value={currentWiki?.id ?? ''}
            onChange={e => onSelect(wikis.find(w => w.id === parseInt(e.target.value)))}
            disabled={wikis.length === 0}
          >
            {wikis.length === 0 && <option value="">No wikis yet</option>}
            {wikis.map(w => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>

          <button className="btn btn-sm" onClick={() => setCreating(true)}>+ New</button>

          {currentWiki && !confirmDelete && (
            <button
              className="btn-icon"
              title={`Delete wiki "${currentWiki.name}"`}
              onClick={() => setConfirmDelete(true)}
            >
              🗑
            </button>
          )}

          {confirmDelete && (
            <span className="inline-confirm">
              Delete "{currentWiki.name}"?&nbsp;
              <button className="btn btn-sm btn-danger" onClick={handleDelete}>Yes</button>
              <button className="btn btn-sm btn-outline" onClick={() => setConfirmDelete(false)}>No</button>
            </span>
          )}
        </>
      ) : (
        <form className="wiki-create-form" onSubmit={handleCreate}>
          <input
            autoFocus
            placeholder="Wiki name..."
            value={name}
            onChange={e => setName(e.target.value)}
          />
          <button className="btn btn-sm" type="submit" disabled={!name.trim()}>Create</button>
          <button className="btn btn-sm btn-outline" type="button" onClick={() => { setCreating(false); setName('') }}>
            Cancel
          </button>
        </form>
      )}
    </div>
  )
}
