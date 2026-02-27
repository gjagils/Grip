# CLAUDE.md — CI/CD & Deployment Instructies

## Overzicht

Dit project gebruikt een geautomatiseerde CI/CD pipeline:

```
Claude Code → GitHub (push/merge) → GitHub Actions (build & push image) → Tailscale → Portainer API → Synology Docker update
```

## Architectuur

- **Code:** GitHub repository
- **Container Registry:** GitHub Container Registry (ghcr.io)
- **CI/CD:** GitHub Actions (build on merge to main)
- **Netwerk:** Tailscale (GitHub Actions runner joins tailnet om Synology te bereiken)
- **Orchestratie:** Portainer stacks op Synology NAS (Community Edition)
- **Runtime:** Docker op Synology

## Hoe secrets werken

Secrets (API keys, tokens) worden **nooit** in de git repo opgeslagen. In plaats daarvan:

1. Secrets staan als **GitHub Secrets** op de repository
2. De GitHub Actions workflow leest de docker-compose.yml uit de repo
3. De workflow stuurt de compose-inhoud + secrets via de **Portainer API** naar de Synology
4. Portainer injecteert de secrets als environment variables in de container

### Secrets in docker-compose.yml

Gebruik `${VARIABLE_NAME}` syntax voor secrets. Deze worden door Portainer vervangen:

```yaml
environment:
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

## GitHub Secrets (per repository)

| Secret | Waarde | Herbruikbaar? |
|--------|--------|---------------|
| `TAILSCALE_AUTHKEY` | Tailscale auth key (reusable + ephemeral) | Ja, zelfde voor alle repos |
| `PORTAINER_API_TOKEN` | Portainer API access token | Ja, zelfde voor alle repos |
| `PORTAINER_URL` | `http://100.65.249.84:9000` (Tailscale IP) | Ja, zelfde voor alle repos |
| `PORTAINER_ENDPOINT_ID` | Endpoint ID uit Portainer | Ja, zelfde voor alle repos |
| `PORTAINER_STACK_ID` | Stack ID uit Portainer URL | **Nee, uniek per project** |
| `ANTHROPIC_API_KEY` | Anthropic API key voor Claude | Per project, indien nodig |

## Tech Stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLite (aiosqlite voor async)
- **Templates:** Jinja2
- **AI:** Anthropic Claude API
- **Frontend:** Vanilla JS + CSS (PWA)

## Lokaal ontwikkelen

```bash
pip install -e .
uvicorn grip.web:app --reload
```

## Commit conventie

Gebruik Conventional Commits:
- `feat:` — nieuwe feature
- `fix:` — bugfix
- `docs:` — documentatie
- `chore:` — onderhoud, dependencies
- `refactor:` — code refactoring
