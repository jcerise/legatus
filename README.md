# Legatus

A command-and-control system for software engineering agents. Legatus deploys autonomous AI agents as ephemeral operatives, coordinated through a central command structure, to execute engineering campaigns against your codebase.

In the Roman legion, the *legatus legionis* commanded thousands from a single seat of authority. This system operates on the same principle: one orchestrator dispatches agents, tracks their campaigns, and consolidates their conquests into your repository. A *praefectus* (PM agent) surveys the terrain and decomposes orders into tactical objectives, then dev agents are dispatched sequentially to execute each objective in turn.

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
- **PM Agent (praefectus)** -- Analyses the battlefield and decomposes a campaign into sequential tactical objectives. Presents the battle plan for your approval before any operative is deployed
- **Dev Agents (milites)** -- Ephemeral Docker containers, each running Claude Code against the workspace. Deployed on command, destroyed on completion. Execute one objective at a time
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

# Issue campaign orders (praefectus decomposes, then dev agents execute)
legion start "Implement a REST endpoint for user authentication"

# Bypass the praefectus -- send a lone operative directly
legion start --direct "Fix the typo in the README"

# Survey the battlefield
legion status
legion status --watch

# Review agent reports
legion logs
legion logs --follow

# Rule on the praefectus' battle plan
legion approve
legion reject <checkpoint-id> "Consolidate the flanks"
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
```

Task state lives in Redis. Long-term memory is stored in Mem0, keyed by project name. All agent operations are confined to a sandboxed `workspace/` directory. Your source remains untouched unless you direct otherwise.

## Current Disposition

**Phase 1** -- the core command structure is operational. The orchestrator accepts tasks, spawns agents, and tracks state.

**Phase 2 (in progress)** -- the *praefectus* has taken the field. Campaign orders are now decomposed into tactical objectives by a PM agent before dev agents are dispatched. The commander (you) reviews and approves the battle plan via checkpoints before any operative touches the codebase. Objectives are executed sequentially, each building on the conquests of the last.

The following campaigns remain:

- Parallel agent operations (branch-per-agent, orchestrator-managed merges)
- QA agent (*tesserarius*) -- automated test generation and verification
- Review agent (*optio*) -- code review before task completion
- Architect agent (*praefectus castrorum*) -- high-level design and ADRs
- Agent container hardening (error recovery, resource limits)
