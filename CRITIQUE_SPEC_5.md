# Critique & Spec 5

## Criticism

**JWT auth signs and verifies tokens with a hardcoded default secret — `"dev-secret-change-in-production"` — so any deployment that doesn't set `JWT_SECRET` (the default path) uses a publicly-known signing key. Anyone can forge a valid token for any user and any role, including `admin`, with no credentials. The project ships AWS deployment docs, so this is a real production exposure, not a dev-only convenience.**

The secret falls back to a literal string (`auth.py:6-7`):

```python
_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
_ALGO = "HS256"
```

and both signing and verification use it (`auth.py:19-30`):

```python
def create_token(user_id: int, username: str, role: str) -> str:
    return jwt.encode({...}, _SECRET, ...)

def decode_token(token: str) -> dict:
    return jwt.decode(token, _SECRET, algorithms=[_ALGO])
```

HS256 is symmetric: the same secret signs and verifies. If that secret is the shipped default, an attacker who has ever seen this source (it's in the repo) can mint a token like `{"user_id": 1, "username": "whoever", "role": "admin"}`, sign it with `"dev-secret-change-in-production"`, and the server accepts it as genuine. No password, no registration, no interaction with the victim.

This converts every other access control into nothing:

| Control | Effect once the secret is known |
|---------|-------------------------------|
| Login / bcrypt password hashing (`auth.py:12-16`) | Bypassed — a forged token never touches the password path |
| Admin-only endpoints (`/api/admin/users`, role patch, user delete — `main.py:173-209`) | Fully accessible by forging `role: "admin"` |
| Per-user wiki ownership | Impersonate any `user_id` to read/modify/delete their wikis |
| The prior authorization fix (CRITIQUE_SPEC_3) | Moot — authorization keys off a token the attacker now controls |

Two factors make the default the *likely* state in practice:

1. **Silent fallback.** Nothing fails, warns, or refuses to start when `JWT_SECRET` is unset — the app boots normally on the known key. An operator gets no signal they're running with a public secret.
2. **Deployment is in scope.** `AWS_DEPLOYMENT.md` / `DEPLOYMENT.md` describe putting this on the internet. A deploy checklist that omits one env var yields a fully forgeable auth system reachable by anyone.

For an app that added authentication *and* a prior authorization hardening pass, signing the whole scheme with a committed default secret undoes both.

---

## Spec

### Goal

Make it impossible to run with a known/default signing secret in any non-development context: no `JWT_SECRET`, no service (or no auth) — never a silent fall back to a shipped constant.

### Approach

Fail-closed on startup when a real secret isn't configured, outside an explicit dev mode. Provide a clear dev affordance so local work stays easy, but production cannot inherit it by accident.

### Specific Changes

**1. `auth.py` — no usable default secret.** Replace the fallback with:
```python
_SECRET = os.getenv("JWT_SECRET")
if not _SECRET:
    if os.getenv("WIKIMANIA_ENV", "dev") == "dev":
        _SECRET = "dev-secret-change-in-production"
        logging.warning("JWT_SECRET unset — using insecure dev secret. Do NOT use in production.")
    else:
        raise RuntimeError("JWT_SECRET must be set when WIKIMANIA_ENV != dev")
```
So production (`WIKIMANIA_ENV=production`) refuses to start without a real secret, while dev keeps the convenience *with a loud warning*.

**2. Enforce a minimum secret strength.** Reject secrets shorter than, say, 32 chars (or equal to the known dev string) outside dev, so a too-short/placeholder value can't slip through.

**3. Startup self-check + docs.** Log (without printing the secret) whether a configured secret is in use. Update `DEPLOYMENT.md` / `AWS_DEPLOYMENT.md` to list `JWT_SECRET` as a required, must-be-generated variable (e.g. `openssl rand -hex 32`) with a prominent callout, not an optional tuning knob.

**4. Token invalidation note.** Document that rotating `JWT_SECRET` invalidates all existing tokens (acceptable, and a feature for incident response). Optionally support a small key list (current + previous) to allow graceful rotation.

### Acceptance Criteria

1. With `WIKIMANIA_ENV=production` and no `JWT_SECRET`, the app refuses to start (clear error), rather than booting on the default.
2. With `WIKIMANIA_ENV=production` and `JWT_SECRET` set to a strong value, auth works normally.
3. In dev with no `JWT_SECRET`, the app starts but logs a prominent warning; a token forged with the dev secret is accepted only in dev.
4. A secret equal to the known dev string (or shorter than the minimum) is rejected outside dev.
5. Deployment docs list `JWT_SECRET` as required with a generation command; a test asserts the production-mode startup guard.
