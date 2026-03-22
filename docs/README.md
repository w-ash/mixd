# Mixd Documentation

Central index for all project documentation, organized by audience.

## User Documentation

Guides and references for using mixd — writing workflows, syncing likes, running CLI commands.

| Document | Type | Description |
|----------|------|-------------|
| [guides/workflows.md](guides/workflows.md) | Guide | Workflow authoring and node catalog |
| [guides/likes-sync.md](guides/likes-sync.md) | Guide | Cross-service likes synchronization |
| [guides/cli.md](guides/cli.md) | Reference | CLI commands and options |

## Developer Documentation

Architecture, setup, and specs for contributing to mixd.

| Document | Type | Description |
|----------|------|-------------|
| [development.md](development.md) | Getting started | Developer setup, versioning, recipes |
| [deployment.md](deployment.md) | Operations | Local dev, Fly.io deploy, CI/CD pipeline |
| [dev-setup-guide/](dev-setup-guide/) | Getting started | Step-by-step environment setup |
| [architecture/database.md](architecture/database.md) | Reference | Schema, relationships, migrations |
| [web-ui/03-api-contracts.md](web-ui/03-api-contracts.md) | Reference | REST API endpoints and contracts |
| [architecture/](architecture/) | Architecture | Layers, patterns, data model, tech stack |
| [web-ui/](web-ui/) | Specs | Web UI user flows and frontend architecture |
| [backlog/](backlog/) | Planning | Roadmap and task breakdowns |

## Quick Navigation

- **Using mixd?** → [guides/](guides/) for workflows, likes sync, and CLI
- **New to the codebase?** → [development.md](development.md) then [architecture/](architecture/)
- **Working on the web UI?** → [web-ui/README.md](web-ui/README.md)
- **Planning work?** → [backlog/README.md](backlog/README.md)

## Maintenance

Docs live alongside code — update them in the same PR that changes behavior.
One source of truth per concept: if information exists in two places, one should be a pointer to the other.
