# GitHub Deployment — wikimania

Full-stack app with **existing Dockerfiles** for both tiers:
- Backend: FastAPI (`backend/`, entry `backend/main.py`, `backend/Dockerfile`)
- Frontend: React/Vite (`frontend/`, `frontend/Dockerfile`, build → `frontend/dist/`)

This repo already has `DEPLOYMENT.md` and `AWS_DEPLOYMENT.md`. This file covers the
**GitHub-native** path: Pages for the static frontend and GHCR for both container images.

---

## 1. Prerequisites
- Own GitHub repo: `gh repo create wikimania --source=. --private --push`
- **Settings → Pages → Source: GitHub Actions**

## 2. Frontend → GitHub Pages
Set the base path in `frontend/vite.config.js`: `base: '/wikimania/'`, and set
`VITE_API_BASE` in `frontend/.env.production` to your deployed backend URL.

`.github/workflows/deploy-frontend.yml`:
```yaml
name: Deploy frontend to Pages
on: { push: { branches: [main] } }
permissions: { contents: read, pages: write, id-token: write }
jobs:
  build:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: frontend } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
      - run: npm run build
      - uses: actions/upload-pages-artifact@v3
        with: { path: frontend/dist }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: '${{ steps.deployment.outputs.page_url }}' }
    steps: [ { id: deployment, uses: actions/deploy-pages@v4 } ]
```

## 3. Both images → GHCR

Because the Dockerfiles already exist, just build and push them.
`.github/workflows/images.yml`:
```yaml
name: Build & push images
on: { push: { branches: [main] } }
permissions: { contents: read, packages: write }
jobs:
  images:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - { ctx: backend,  name: wikimania-api }
          - { ctx: frontend, name: wikimania-web }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: '${{ github.actor }}', password: '${{ secrets.GITHUB_TOKEN }}' }
      - uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.ctx }}
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/${{ matrix.name }}:latest
```

Deploy the images to any container host. For a one-command local run, use the existing
`docker-compose` setup if present, or:
```bash
docker run -p 8000:8000 ghcr.io/<owner>/wikimania-api:latest
docker run -p 8080:80   ghcr.io/<owner>/wikimania-web:latest
```

## 4. Notes
- Prefer the GHCR images for the frontend too if you want SSR/runtime config; use Pages
  only for the pure-static build.
- Keep secrets (API keys) out of the repo — set them on the backend host, not in Pages.
