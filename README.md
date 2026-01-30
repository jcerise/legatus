# Legatus

A command-and-control system for software engineering agents. Legatus deploys autonomous AI agents as ephemeral operatives, coordinated through a central command structure, to execute engineering tasks against your codebase.

In the Roman legion, the *legatus legionis* commanded thousands from a single seat of authority. This system operates on the same principle: one orchestrator dispatches agents, tracks their campaigns, and consolidates their conquests into your repository.

## Architecture

```
                        +-------------+
    legion CLI ---------> | Orchestrator | --------> Agent Containers
    (field orders)      | (command)    |           (operatives)
                        +------+------+
                               |
                        +------+------+
                        |    Redis    |    Mem0
                        | (dispatch) |  (intelligence)
                        +-------------+
```

- **Orchestrator** -- FastAPI service (port 8420) that receives orders, manages the campaign ledger, and spawns agent containers via Docker
- **Agents** -- Ephemeral Docker containers, each running Claude Code against the workspace. Deployed on command, destroyed on completion
- **Redis** -- State store and courier system. Task records, agent status, and pub/sub messaging between all components
- **Mem0** -- Long-term intelligence. Agents store and retrieve institutional knowledge across campaigns
- **CLI (`legion`)** -- Your interface to issue orders and observe the field

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker & Docker Compose
- An Anthropic API key
- An OpenAI API key (for Mem0 embeddings)

## Deployment

```bash
# Install the CLI
uv venv && uv pip install -e "."

# Configure your keys
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and OPENAI_API_KEY

# Raise the standard -- build and start all services
make build && make up
```

## Field Commands

```bash
# Initialize a project workspace
legion init

# Dispatch an agent with orders
legion start "Implement a REST endpoint for user authentication"

# Survey the battlefield
legion status
legion status --watch

# Review agent reports
legion logs
legion logs --follow

# Rule on agent checkpoints (when human approval is required)
legion approve <checkpoint-id>
legion reject <checkpoint-id>
```

## Standing Orders

```bash
make build       # Forge all container images
make up          # Deploy the garrison
make down        # Withdraw all forces
make logs        # Read dispatches from the field
make ps          # Muster roll -- show running services
make clean       # Raze the camp -- stop services, remove volumes
make lint        # Inspect the ranks
make test        # Drill exercises
```

## Campaign Structure

When `legion init` is run, a `.legatus/` directory is established as the local command post:

```
.legatus/
  config.yaml      # Standing orders and configuration
  tasks/            # Campaign records
  memory/           # Local intelligence cache
```

All agent operations are confined to a sandboxed `workspace/` directory. Your source remains untouched unless you direct otherwise.

## Current Disposition

This is **Phase 1** -- the core command structure is operational. The orchestrator accepts tasks, spawns agents, and tracks state. The following campaigns remain:

- Agent container hardening (Claude Code auth, error recovery)
- Multi-agent coordination (task decomposition, parallel operations)
- Review and approval workflows
- Checkpoint-based human-in-the-loop control
