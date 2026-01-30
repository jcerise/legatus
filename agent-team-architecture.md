# Agent Team Architecture Design

## Overview

A multi-agent software engineering system that orchestrates specialized AI agents to collaboratively build software projects. The system runs entirely in local Docker containers, uses Claude Code as the primary LLM with optional local model support, and provides a CLI interface with hybrid (autonomous + supervised) orchestration.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              HOST MACHINE                                        │
│                                                                                  │
│  ┌─────────────┐                                                                │
│  │   CLI       │  $ team start "Build a REST API for todo management"           │
│  │  (Python)   │  $ team status                                                  │
│  │             │  $ team approve 3                                               │
│  └──────┬──────┘  $ team chat "focus on auth first"                             │
│         │                                                                        │
│         │ HTTP/WebSocket                                                         │
│         ▼                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                     DOCKER COMPOSE NETWORK                                │   │
│  │                                                                           │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐ │   │
│  │  │                      ORCHESTRATOR CONTAINER                          │ │   │
│  │  │                                                                      │ │   │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │ │   │
│  │  │  │ Task Manager │  │ Agent Router │  │  Checkpoint  │              │ │   │
│  │  │  │              │  │              │  │   Manager    │              │ │   │
│  │  │  │ • Queue      │  │ • Assigns    │  │              │              │ │   │
│  │  │  │ • Priority   │  │ • Load bal   │  │ • Approval   │              │ │   │
│  │  │  │ • Deps       │  │ • Health     │  │ • Rollback   │              │ │   │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘              │ │   │
│  │  │                            │                                        │ │   │
│  │  │                   ┌────────┴────────┐                              │ │   │
│  │  │                   ▼                 ▼                              │ │   │
│  │  │            ┌────────────┐    ┌────────────┐                        │ │   │
│  │  │            │  REST API  │    │ WebSocket  │                        │ │   │
│  │  │            │  (FastAPI) │    │  (Events)  │                        │ │   │
│  │  │            └────────────┘    └────────────┘                        │ │   │
│  │  └─────────────────────────────────────────────────────────────────────┘ │   │
│  │                                    │                                      │   │
│  │         ┌──────────────────────────┼──────────────────────────┐          │   │
│  │         │                          │                          │          │   │
│  │         ▼                          ▼                          ▼          │   │
│  │  ┌────────────┐            ┌────────────┐            ┌────────────┐     │   │
│  │  │ PM AGENT   │            │ DEV AGENT  │            │ QA AGENT   │     │   │
│  │  │ CONTAINER  │            │ CONTAINER  │            │ CONTAINER  │     │   │
│  │  │            │            │  (1..N)    │            │            │     │   │
│  │  │ Claude Code│            │ Claude Code│            │ Claude Code│     │   │
│  │  │ or Local   │            │ or Local   │            │ or Local   │     │   │
│  │  └─────┬──────┘            └─────┬──────┘            └─────┬──────┘     │   │
│  │        │                         │                         │            │   │
│  │        └─────────────────────────┼─────────────────────────┘            │   │
│  │                                  │                                       │   │
│  │                                  ▼                                       │   │
│  │  ┌───────────────────────────────────────────────────────────────────┐  │   │
│  │  │                      SHARED SERVICES                               │  │   │
│  │  │                                                                    │  │   │
│  │  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  │  │   │
│  │  │  │   Redis    │  │   Mem0     │  │  Ollama    │  │  Git Ops   │  │  │   │
│  │  │  │            │  │            │  │ (Optional) │  │            │  │  │   │
│  │  │  │ • Tasks    │  │ • Memory   │  │            │  │ • Commits  │  │  │   │
│  │  │  │ • Messages │  │ • Vectors  │  │ • Local    │  │ • Branches │  │  │   │
│  │  │  │ • State    │  │ • Search   │  │   Models   │  │ • History  │  │  │   │
│  │  │  └────────────┘  └────────────┘  └────────────┘  └────────────┘  │  │   │
│  │  └───────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                          │   │
│  │  ┌───────────────────────────────────────────────────────────────────┐  │   │
│  │  │                      WORKSPACE VOLUME                              │  │   │
│  │  │  /workspace (mounted from host ~/projects/current-project)        │  │   │
│  │  └───────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  📁 ~/projects/my-app ◄─────────────── Bidirectional mount                      │
│  📁 ~/.agent-team/    ◄─────────────── Config, logs, global memory              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. CLI Interface

The primary interface for interacting with the system.

```bash
# Project Management
team init                          # Initialize agent-team in current directory
team start "Build a REST API..."   # Start a new task/project
team start --spec requirements.md  # Start from a spec file

# Monitoring
team status                        # Show current state of all agents/tasks
team status --watch                # Live updating status
team logs                          # Show recent activity
team logs --agent dev              # Logs from specific agent

# Control Flow (Hybrid Mode)
team approve <task-id>             # Approve a checkpoint
team reject <task-id> "reason"     # Reject with feedback
team pause                         # Pause all agents
team resume                        # Resume work

# Interaction
team chat "focus on the API first" # Send guidance to orchestrator
team ask "why did you choose X?"   # Ask about a decision

# Agent Management
team agents                        # List all agents and their status
team scale dev 3                   # Scale dev agents to 3 instances
team restart qa                    # Restart a specific agent

# Memory Management
team memory show                   # Show what the team remembers
team memory forget "bad pattern"   # Remove a memory
team memory export                 # Export project memory

# Utilities
team cost                          # Show token usage and estimated cost
team history                       # Show task history
team rollback <commit>             # Rollback to a previous state
```

### 2. Agent Definitions

Each agent has a specific role, capabilities, and constraints.

#### Agent Configuration Schema

```yaml
# agents/pm.yaml
agent:
  name: "Product Manager"
  id: "pm"
  role: "product_manager"
  
  model:
    primary: "claude-code"           # Use Claude Code
    fallback: "ollama/llama3.2"      # Fallback to local
    
  capabilities:
    - "read_files"
    - "write_specs"
    - "create_tasks"
    - "approve_work"
    - "query_memory"
    
  constraints:
    - "cannot_write_code"
    - "cannot_run_commands"
    
  checkpoints:
    - trigger: "task_breakdown_complete"
      approval: "optional"           # User can auto-approve
    - trigger: "feature_complete"
      approval: "required"           # Must get user approval
      
  system_prompt: |
    You are a Product Manager agent working on a software engineering team.
    
    Your responsibilities:
    - Break down high-level requirements into actionable user stories
    - Prioritize tasks based on dependencies and value
    - Review completed work against requirements
    - Maintain the product backlog
    
    You communicate with other agents through the task system.
    You CANNOT write code directly - delegate to Dev agents.
    
    When creating tasks, use this format:
    TASK: <title>
    ASSIGNEE: <agent-role>
    PRIORITY: <1-5>
    DEPENDS_ON: <task-ids or none>
    ACCEPTANCE_CRITERIA:
    - <criterion 1>
    - <criterion 2>
```

#### Agent Roster

| Agent | Model Recommendation | Purpose | Checkpoint Triggers |
|-------|---------------------|---------|---------------------|
| **PM** | Claude (smart) | Requirements, prioritization, acceptance | Task breakdown, feature acceptance |
| **Architect** | Claude (smart) | System design, tech decisions | Architecture decisions, major refactors |
| **Dev** | Claude Code | Implementation, debugging | None (autonomous) |
| **QA** | Local model OK | Test writing, test execution | Test failures (notifies, doesn't block) |
| **Reviewer** | Claude (smart) | Code review, improvements | Security issues, major concerns |
| **Docs** | Local model OK | Documentation | None (autonomous) |

### 3. Task System

Tasks flow through the system with clear states and dependencies.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TASK LIFECYCLE                                     │
│                                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│   │ CREATED │───▶│ PLANNED │───▶│ ACTIVE  │───▶│ REVIEW  │───▶│  DONE   │ │
│   └─────────┘    └─────────┘    └────┬────┘    └────┬────┘    └─────────┘ │
│        │              │              │              │                       │
│        │              │              ▼              ▼                       │
│        │              │         ┌─────────┐   ┌─────────┐                  │
│        │              │         │ BLOCKED │   │ REJECTED│                  │
│        │              │         └─────────┘   └────┬────┘                  │
│        │              │                            │                        │
│        │              └────────────────────────────┘                        │
│        │                      (back to PLANNED)                             │
│        │                                                                    │
│        ▼                                                                    │
│   CHECKPOINT (if required)                                                  │
│   └── Waits for user approval before proceeding                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Task Schema

```json
{
  "id": "task_001",
  "title": "Implement user authentication endpoint",
  "description": "Create POST /api/auth/login endpoint...",
  "type": "feature",
  "status": "active",
  "priority": 1,
  "created_by": "pm",
  "assigned_to": "dev_1",
  "depends_on": ["task_000"],
  "blocks": ["task_002", "task_003"],
  "acceptance_criteria": [
    "Endpoint accepts email/password",
    "Returns JWT on success",
    "Returns 401 on invalid credentials",
    "Has rate limiting"
  ],
  "checkpoint": {
    "required": false,
    "status": null
  },
  "artifacts": [
    {"type": "file", "path": "src/auth/login.py"},
    {"type": "file", "path": "tests/test_auth.py"}
  ],
  "history": [
    {"timestamp": "...", "event": "created", "by": "pm"},
    {"timestamp": "...", "event": "assigned", "to": "dev_1"},
    {"timestamp": "...", "event": "started", "by": "dev_1"}
  ]
}
```

### 4. Memory System

Three-tier memory architecture using Mem0 + Redis.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MEMORY ARCHITECTURE                                │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                     WORKING MEMORY (Redis - ephemeral)                 │  │
│  │                                                                        │  │
│  │  Scope: Current session / active task                                 │  │
│  │  TTL: Cleared on task completion                                      │  │
│  │                                                                        │  │
│  │  Contents:                                                             │  │
│  │  • Current task context and requirements                              │  │
│  │  • Files currently being edited (content cache)                       │  │
│  │  • Recent agent messages (last ~20)                                   │  │
│  │  • Active errors/warnings being addressed                             │  │
│  │                                                                        │  │
│  │  Key Pattern: working:{project}:{agent}:{key}                         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│                                    ▼ Summarized on task completion           │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                   PROJECT MEMORY (Mem0 - persistent)                   │  │
│  │                                                                        │  │
│  │  Scope: Per-project, persists across sessions                         │  │
│  │  Storage: Vector embeddings + Redis JSON                              │  │
│  │                                                                        │  │
│  │  Contents:                                                             │  │
│  │  • Architecture decisions and rationale                               │  │
│  │  • "We chose FastAPI because..."                                      │  │
│  │  • "Auth is handled by..."                                            │  │
│  │  • Coding patterns established in this project                        │  │
│  │  • "We use repository pattern for data access"                        │  │
│  │  • Known issues and workarounds                                       │  │
│  │  • "SQLite has issues with concurrent writes, use WAL mode"          │  │
│  │  • File/module purposes                                               │  │
│  │  • "src/utils/validators.py handles all input validation"            │  │
│  │                                                                        │  │
│  │  Namespace: project:{project_id}                                      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    │                                         │
│                                    ▼ Patterns extracted over time            │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    GLOBAL MEMORY (Mem0 - persistent)                   │  │
│  │                                                                        │  │
│  │  Scope: All projects, represents learned preferences                  │  │
│  │  Storage: Vector embeddings + Redis JSON                              │  │
│  │                                                                        │  │
│  │  Contents:                                                             │  │
│  │  • Your coding preferences                                            │  │
│  │  • "User prefers type hints in Python"                               │  │
│  │  • "User likes descriptive variable names"                           │  │
│  │  • Framework/tool preferences                                         │  │
│  │  • "User prefers pytest over unittest"                               │  │
│  │  • "User likes Tailwind for CSS"                                     │  │
│  │  • Common patterns you use                                            │  │
│  │  • Anti-patterns to avoid                                             │  │
│  │  • "User dislikes excessive comments"                                │  │
│  │                                                                        │  │
│  │  Namespace: global:{user_id}                                          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Memory Operations

```python
# Memory queries are automatically injected into agent context

# When Dev agent starts a task:
relevant_memories = memory.search(
    query=task.description,
    namespaces=["working:*", f"project:{project_id}", "global:*"],
    limit=10
)

# Injected into agent prompt:
"""
## Relevant Context from Memory

### Project Knowledge
- We're using FastAPI with SQLAlchemy ORM
- Auth uses JWT tokens stored in httponly cookies
- All endpoints should use dependency injection for DB sessions

### Your Preferences  
- Use type hints on all function signatures
- Prefer descriptive names over comments
- Use pytest with fixtures for testing
"""
```

### 5. Communication System

Agents communicate through a Redis-based message bus.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MESSAGE FLOW PATTERNS                                 │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  PATTERN 1: Task Assignment (Orchestrator → Agent)                   │    │
│  │                                                                      │    │
│  │  Orchestrator ──────▶ Redis Queue ──────▶ Agent                     │    │
│  │                       "tasks:{agent_id}"                             │    │
│  │                                                                      │    │
│  │  Message: {                                                          │    │
│  │    "type": "task_assignment",                                       │    │
│  │    "task_id": "task_001",                                           │    │
│  │    "priority": 1                                                    │    │
│  │  }                                                                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  PATTERN 2: Status Updates (Agent → Orchestrator)                    │    │
│  │                                                                      │    │
│  │  Agent ──────▶ Redis PubSub ──────▶ Orchestrator                    │    │
│  │               "events:agent"          (+ WebSocket to CLI)          │    │
│  │                                                                      │    │
│  │  Message: {                                                          │    │
│  │    "type": "task_update",                                           │    │
│  │    "task_id": "task_001",                                           │    │
│  │    "status": "completed",                                           │    │
│  │    "artifacts": ["src/auth.py"]                                     │    │
│  │  }                                                                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  PATTERN 3: Agent-to-Agent (via Orchestrator)                        │    │
│  │                                                                      │    │
│  │  Dev ──────▶ Orchestrator ──────▶ QA                                │    │
│  │       "ready for testing"    routes    "test task_001"              │    │
│  │                                                                      │    │
│  │  Agents don't talk directly - orchestrator mediates                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  PATTERN 4: Checkpoint (Agent → User)                                │    │
│  │                                                                      │    │
│  │  Agent ──────▶ Orchestrator ──────▶ CLI (notification)              │    │
│  │       "checkpoint: approve architecture?"                           │    │
│  │                                                                      │    │
│  │  Task is BLOCKED until: team approve <task_id>                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6. Model Configuration

Primary model is Claude Code running in autonomous mode within sandboxed containers.

```yaml
# config/models.yaml

models:
  # Primary: Claude Code CLI (runs inside agent container)
  claude-code:
    type: "claude-code-cli"
    command: "claude"
    args: 
      - "--dangerously-skip-permissions"  # Safe because we're sandboxed
      - "--output-format"
      - "json"                             # Structured output for parsing
    timeout: 600                           # 10 minutes max per invocation
    
  # Future: Direct API access for non-coding tasks
  claude-api:
    type: "anthropic-api"
    model: "claude-sonnet-4-20250514"
    api_key: "${ANTHROPIC_API_KEY}"
    
  # Future: Local models for cost optimization
  ollama-llama:
    type: "ollama"
    model: "llama3.2"
    endpoint: "http://ollama:11434"
    
  ollama-codellama:
    type: "ollama" 
    model: "codellama:13b"
    endpoint: "http://ollama:11434"

# Phase 1: All agents use Claude Code
# Phase 4: Introduce model routing for cost optimization
agent_models:
  dev:
    primary: "claude-code"
    # Future: fallback to local for simple tasks
    
  pm:
    primary: "claude-code"  # Phase 2
    
  qa:
    primary: "claude-code"  # Phase 2, consider ollama-codellama later
    
  reviewer:
    primary: "claude-code"  # Phase 2
    
  docs:
    primary: "claude-code"  # Phase 2, consider ollama-llama later
```

#### Claude Code Invocation Pattern

```python
# agent/claude_wrapper.py

import subprocess
import json

def invoke_claude_code(prompt: str, working_dir: str) -> dict:
    """
    Invoke Claude Code CLI with structured output.
    
    Returns dict with:
      - success: bool
      - output: str (captured stdout)
      - files_modified: list[str]
      - error: str | None
    """
    result = subprocess.run(
        [
            "claude",
            "--dangerously-skip-permissions",
            "--output-format", "json",
            "--max-turns", "50",          # Limit iterations
            "-p", prompt                   # The task prompt
        ],
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=600
    )
    
    # Parse structured output
    # Report progress via Redis pubsub
    # Extract files modified for git commit
    
    return {
        "success": result.returncode == 0,
        "output": result.stdout,
        "error": result.stderr if result.returncode != 0 else None
    }
```

### 7. Checkpoint System (Hybrid Mode)

Configurable approval gates for human oversight.

```yaml
# config/checkpoints.yaml

checkpoints:
  # Approval levels
  levels:
    auto:        # Automatically approved, just logged
    optional:    # User notified, auto-approves after timeout
    required:    # Blocks until user approves
    
  # Default timeouts for optional checkpoints
  timeouts:
    optional: 300  # 5 minutes, then auto-approve
    
  # Checkpoint definitions
  triggers:
    # Project-level checkpoints
    - name: "project_start"
      description: "Initial task breakdown and architecture"
      level: "required"
      agent: "pm"
      
    - name: "architecture_decision"
      description: "Major architectural choices"
      level: "required"
      agent: "architect"
      
    # Feature-level checkpoints  
    - name: "feature_complete"
      description: "Feature ready for review"
      level: "optional"
      agent: "dev"
      
    - name: "tests_failing"
      description: "Tests are failing after implementation"
      level: "auto"  # Just notify, don't block
      agent: "qa"
      
    # Safety checkpoints
    - name: "security_concern"
      description: "Potential security issue found"
      level: "required"
      agent: "reviewer"
      
    - name: "destructive_operation"
      description: "About to delete files or make breaking changes"
      level: "required"
      agent: "*"

  # User can override defaults
  overrides:
    # "I trust the dev agent, don't ask me about features"
    feature_complete: "auto"
```

---

## Directory Structure

```
~/.agent-team/                      # Global config and data
├── config/
│   ├── models.yaml                 # Model configurations
│   ├── checkpoints.yaml            # Checkpoint rules
│   └── preferences.yaml            # User preferences
├── memory/
│   └── global/                     # Global memory storage
├── logs/
│   └── {date}/                     # Daily logs
└── cache/
    └── models/                     # Local model cache

~/projects/my-app/                  # Project directory
├── .agent-team/                    # Project-specific config
│   ├── config.yaml                 # Project overrides
│   ├── agents/                     # Custom agent definitions
│   │   └── custom-agent.yaml
│   ├── memory/                     # Project memory
│   │   └── project.db
│   └── tasks/
│       ├── backlog.json            # Task backlog
│       └── history/                # Completed tasks
├── src/                            # Your actual code
├── tests/
└── docs/
```

---

## Docker Compose Configuration

```yaml
# docker-compose.yaml
#
# NOTE: Agent containers are NOT defined here. They are spawned dynamically
# by the orchestrator on a per-task basis and torn down on completion.
# This compose file defines only the persistent infrastructure.

version: '3.8'

services:
  # ===================
  # ORCHESTRATOR
  # ===================
  orchestrator:
    build: ./containers/orchestrator
    ports:
      - "8420:8420"           # REST API
      - "8421:8421"           # WebSocket (live updates)
    volumes:
      - ${WORKSPACE:-./workspace}:/workspace
      - ${HOME}/.agent-team:/root/.agent-team
      - /var/run/docker.sock:/var/run/docker.sock  # Required to spawn agent containers
    environment:
      - REDIS_URL=redis://redis:6379
      - MEM0_URL=http://mem0:8000
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - AGENT_IMAGE=agent-team/agent:latest
      - WORKSPACE_PATH=${WORKSPACE:-./workspace}
    depends_on:
      redis:
        condition: service_healthy
      mem0:
        condition: service_started
    networks:
      - agent-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8420/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  # ===================
  # SHARED SERVICES
  # ===================
  redis:
    image: redis/redis-stack:latest
    ports:
      - "6379:6379"           # Redis
      - "8001:8001"           # RedisInsight UI (optional, for debugging)
    volumes:
      - redis-data:/data
    networks:
      - agent-network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  mem0:
    build: ./containers/mem0
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379
      - OPENAI_API_KEY=${OPENAI_API_KEY}  # For embeddings (or use local embeddings)
      # Alternative: Use local embeddings with Ollama
      # - EMBEDDING_MODEL=ollama/nomic-embed-text
      # - OLLAMA_API_BASE=http://ollama:11434
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - agent-network

  # ===================
  # OPTIONAL: LOCAL MODELS
  # ===================
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    profiles:
      - local-models          # Only starts with: docker compose --profile local-models up
    networks:
      - agent-network

volumes:
  redis-data:
  ollama-data:

networks:
  agent-network:
    driver: bridge
```

### Agent Container (Spawned Dynamically)

The orchestrator spawns agent containers using the Docker API. Each agent container:

```yaml
# This is the template used by the orchestrator, not a compose service
# containers/agent/Dockerfile defines the image

agent-container-template:
  image: agent-team/agent:latest
  environment:
    - TASK_ID=${task_id}
    - AGENT_ROLE=${role}
    - REDIS_URL=redis://redis:6379
    - MEM0_URL=http://mem0:8000
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
  volumes:
    - ${WORKSPACE}:/workspace          # Read-write for dev agents
    - ${HOME}/.agent-team:/root/.agent-team:ro
  networks:
    - agent-network
  # Container is removed after task completion
  auto_remove: true
```

### Spawning Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        AGENT LIFECYCLE                                   │
│                                                                          │
│  1. Task assigned to dev agent                                          │
│     └── Orchestrator calls Docker API                                   │
│                                                                          │
│  2. Container created with:                                             │
│     ├── Task ID and role injected as env vars                          │
│     ├── Workspace volume mounted                                        │
│     ├── Network connected to agent-network                             │
│     └── Mem0 retrieves relevant memories → injected into prompt        │
│                                                                          │
│  3. Agent executes task                                                 │
│     ├── Claude Code runs with --dangerously-skip-permissions           │
│     ├── Progress reported via Redis pubsub                             │
│     └── Files modified in /workspace                                   │
│                                                                          │
│  4. Task completes                                                      │
│     ├── Agent reports completion status                                │
│     ├── Mem0 extracts learnings from task                              │
│     ├── Git commit created (if files changed)                          │
│     └── Container automatically removed                                │
│                                                                          │
│  5. Next task                                                           │
│     └── Fresh container spawned (clean state)                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Workflow Examples

### Example 1: Starting a New Project

```bash
$ cd ~/projects
$ mkdir my-api && cd my-api
$ team init

✓ Initialized agent-team in /home/user/projects/my-api
✓ Created .agent-team/ directory
✓ Default configuration created

$ team start "Build a REST API for a todo list application with user authentication"

🚀 Starting new project...

📋 PM Agent is analyzing requirements...

┌─────────────────────────────────────────────────────────────┐
│ CHECKPOINT: Project Planning Complete                        │
│                                                              │
│ PM Agent has created the following task breakdown:           │
│                                                              │
│ Epic: Todo List API                                          │
│ ├── Task 1: Project Setup (Priority: 1)                     │
│ │   └── Initialize FastAPI project with standard structure  │
│ ├── Task 2: User Authentication (Priority: 1)               │
│ │   ├── 2.1: User model and database schema                 │
│ │   ├── 2.2: Registration endpoint                          │
│ │   ├── 2.3: Login endpoint with JWT                        │
│ │   └── 2.4: Auth middleware                                │
│ ├── Task 3: Todo CRUD (Priority: 2)                         │
│ │   ├── 3.1: Todo model                                     │
│ │   ├── 3.2: Create/Read endpoints                          │
│ │   └── 3.3: Update/Delete endpoints                        │
│ └── Task 4: Testing & Documentation (Priority: 3)           │
│                                                              │
│ Estimated: 8 subtasks, ~2-3 hours autonomous work           │
│                                                              │
│ [A]pprove  [M]odify  [R]eject                               │
└─────────────────────────────────────────────────────────────┘

$ team approve

✓ Plan approved. Agents are starting work...

🔨 Dev Agent picked up: Task 1 - Project Setup
📝 Architect Agent is designing: Authentication system
```

### Example 2: Monitoring Progress

```bash
$ team status

┌─────────────────────────────────────────────────────────────┐
│ PROJECT: my-api                                              │
│ STARTED: 15 minutes ago                                      │
│ STATUS: Active                                               │
├─────────────────────────────────────────────────────────────┤
│ AGENTS                                                       │
│ ├── PM        : idle (watching)                             │
│ ├── Architect : active (designing auth system)              │
│ ├── Dev       : active (implementing user model)            │
│ ├── QA        : idle (waiting for testable code)           │
│ └── Reviewer  : idle                                        │
├─────────────────────────────────────────────────────────────┤
│ TASKS                                      PROGRESS          │
│ ├── [✓] Task 1: Project Setup              ████████████ 100% │
│ ├── [→] Task 2.1: User model               ████████░░░░  65% │
│ ├── [ ] Task 2.2: Registration             ░░░░░░░░░░░░   0% │
│ ├── [ ] Task 2.3: Login endpoint           ░░░░░░░░░░░░   0% │
│ └── [ ] ... 4 more tasks                                    │
├─────────────────────────────────────────────────────────────┤
│ RECENT ACTIVITY                                              │
│ • 2m ago  Dev created src/models/user.py                    │
│ • 5m ago  Dev created src/database.py                       │
│ • 8m ago  Architect decided: "Using SQLAlchemy with SQLite" │
│ • 15m ago Project started                                    │
├─────────────────────────────────────────────────────────────┤
│ COST THIS SESSION: ~$0.45 (est.)                            │
└─────────────────────────────────────────────────────────────┘
```

### Example 3: Handling a Checkpoint

```bash
# CLI shows notification
🔔 CHECKPOINT: Architect requests approval

┌─────────────────────────────────────────────────────────────┐
│ Architecture Decision: Authentication System                 │
│                                                              │
│ The Architect proposes:                                      │
│                                                              │
│ • JWT tokens with 24h expiry                                │
│ • Tokens stored in httponly cookies (not localStorage)      │
│ • Refresh tokens with 7-day expiry                          │
│ • Passwords hashed with bcrypt (cost factor 12)             │
│ • Rate limiting: 5 login attempts per minute                │
│                                                              │
│ Rationale: This balances security with usability.           │
│ Cookies prevent XSS token theft. Refresh tokens reduce      │
│ re-authentication friction.                                  │
│                                                              │
│ Files that will be created:                                  │
│ • src/auth/jwt.py                                           │
│ • src/auth/middleware.py                                    │
│ • src/auth/rate_limit.py                                    │
│                                                              │
│ [A]pprove  [M]odify  [R]eject  [?] Ask question            │
└─────────────────────────────────────────────────────────────┘

$ team ask "Why cookies instead of localStorage with Authorization header?"

🤖 Architect: Cookies with httponly flag cannot be accessed by JavaScript, 
   which protects against XSS attacks. If an attacker injects malicious JS,
   they cannot steal the token. The tradeoff is slightly more complex CORS 
   setup, but for a single-domain API this isn't an issue. Would you prefer
   the Authorization header approach instead?

$ team approve

✓ Architecture approved. Dev Agent is proceeding with implementation...
```

### Example 4: Intervention Mid-task

```bash
$ team chat "Actually, let's use PostgreSQL instead of SQLite"

📨 Message sent to orchestrator

🤖 Orchestrator: Understood. I'm updating the plan:
   
   - Pausing current database-related tasks
   - Architect will update the database design
   - Dev will modify existing database code
   
   This will add ~15 minutes to the timeline. Proceeding?

$ team approve

✓ Redirecting agents...

🔨 Dev Agent: Updating src/database.py for PostgreSQL
📝 Architect: Updated docker-compose to include PostgreSQL service
```

---

## Cost Optimization Strategies

### 1. Model Tiering

| Task Type | Recommended Model | Est. Cost |
|-----------|------------------|-----------|
| Architecture, Security Review | Claude Sonnet | $$$ |
| Feature Implementation | Claude Code | $$ |
| Test Writing | Local (CodeLlama) | $ |
| Documentation | Local (Llama) | $ |
| Simple Refactors | Local (CodeLlama) | $ |

### 2. Memory-Based Cost Reduction

```
Without Memory:
  Each task: Full context reload → ~10K tokens input
  10 tasks = ~100K tokens = ~$0.30

With Memory:
  Each task: Relevant context only → ~2K tokens input  
  10 tasks = ~20K tokens = ~$0.06
  
  Savings: 80%
```

### 3. Caching Strategies

- **Embedding Cache**: Reuse embeddings for similar queries
- **Response Cache**: Cache common code patterns
- **File Cache**: Don't re-read unchanged files

---

## Security Considerations

### Container Isolation

```
┌─────────────────────────────────────────────────────────────┐
│ SECURITY BOUNDARIES                                          │
│                                                              │
│  Host Machine                                                │
│  └── Docker Network (isolated)                              │
│      ├── Orchestrator (has docker.sock - controlled)       │
│      ├── Agents (no docker.sock)                           │
│      │   ├── PM Agent: Read-only workspace                 │
│      │   ├── Dev Agent: Read-write workspace               │
│      │   └── QA Agent: Read-write workspace                │
│      └── Services (no workspace access)                    │
│          ├── Redis                                          │
│          ├── Mem0                                           │
│          └── Ollama                                         │
│                                                              │
│  Workspace is ONLY mounted in agent containers              │
│  Agents cannot access host filesystem outside workspace     │
│  Agents cannot spawn other containers                       │
│  Network egress can be restricted per container             │
└─────────────────────────────────────────────────────────────┘
```

### API Key Management

```yaml
# Secrets are injected via environment, never stored in containers
# Use Docker secrets or environment files

# .env (not committed to git)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...  # For embeddings only
```

---

## Implementation Phases

### Phase 1: Core System (Starting Point) ✅ CURRENT TARGET
The prototype includes Mem0 from the start to establish the memory-first architecture.

- [ ] **CLI Foundation**
  - `team init` - Initialize project
  - `team start <prompt>` - Start a task
  - `team status` - Show current state
  - `team approve/reject` - Checkpoint handling
  - `team logs` - View activity

- [ ] **Orchestrator Service**
  - FastAPI server with REST + WebSocket
  - Task queue management (Redis)
  - Single Dev agent spawning (per-task containers)
  - Checkpoint creation and blocking
  - Basic git operations (commit on task completion)

- [ ] **Dev Agent Container**
  - Claude Code with `--dangerously-skip-permissions`
  - Task execution wrapper (captures output, reports status)
  - Memory injection (retrieves relevant context from Mem0)
  - Memory extraction (saves learnings on task completion)
  - Workspace mounted read-write

- [ ] **Mem0 + Redis Stack**
  - Redis Stack for task queue, messages, and vector storage
  - Mem0 for memory management
  - Three namespaces: working, project, global
  - Automatic memory extraction from completed tasks

- [ ] **Docker Compose**
  - Orchestrator container
  - Dev agent container (spawned per-task)
  - Redis Stack container
  - Mem0 container
  - Shared workspace volume

### Phase 2: Multi-Agent Team

**Goal**: Transform from single Dev agent to a coordinated team with specialized roles.

#### 2.1 PM Agent
- [ ] **Requirements Analysis**
  - Accepts high-level prompts and breaks into user stories
  - Creates task dependency graph
  - Assigns priority scores (1-5)
  - Defines acceptance criteria per task
  
- [ ] **Task Lifecycle Management**
  - Reviews completed tasks against acceptance criteria
  - Can approve, reject with feedback, or request changes
  - Maintains project backlog in Redis
  
- [ ] **System Prompt & Constraints**
  ```
  Role: Product Manager
  Can: Read files, create tasks, approve work, query memory
  Cannot: Write code, run commands, modify files directly
  Checkpoint: After initial task breakdown (required)
  ```

#### 2.2 QA Agent
- [ ] **Test Generation**
  - Analyzes code written by Dev agent
  - Generates unit tests, integration tests
  - Follows testing patterns from project memory
  
- [ ] **Test Execution**
  - Runs test suites after Dev completes tasks
  - Reports failures with diagnostics
  - Can request Dev to fix failing tests
  
- [ ] **Coverage Tracking**
  - Monitors test coverage metrics
  - Flags untested code paths
  
- [ ] **System Prompt & Constraints**
  ```
  Role: QA Engineer
  Can: Read/write test files, run tests, read source code
  Cannot: Modify source code (only test files)
  Checkpoint: On test failures (auto - notifies but doesn't block)
  ```

#### 2.3 Reviewer Agent
- [ ] **Code Review**
  - Reviews all code before task completion
  - Checks for: bugs, security issues, style violations, performance
  - Provides inline feedback
  
- [ ] **Standards Enforcement**
  - Validates against project coding standards (from memory)
  - Ensures consistency with existing codebase
  
- [ ] **Security Scanning**
  - Identifies common vulnerabilities (injection, auth issues, etc.)
  - Flags sensitive data exposure
  
- [ ] **System Prompt & Constraints**
  ```
  Role: Code Reviewer
  Can: Read all files, add review comments, approve/block merges
  Cannot: Modify code directly
  Checkpoint: On security concerns (required)
  ```

#### 2.4 Architect Agent
- [ ] **System Design**
  - Makes high-level technical decisions
  - Designs module structure and interfaces
  - Chooses frameworks, libraries, patterns
  
- [ ] **Documentation**
  - Creates/updates architecture decision records (ADRs)
  - Maintains system diagrams
  - Documents API contracts
  
- [ ] **System Prompt & Constraints**
  ```
  Role: Software Architect
  Can: Read/write design docs, create ADRs, define interfaces
  Cannot: Implement features (delegates to Dev)
  Checkpoint: On architecture decisions (required)
  ```

#### 2.5 Agent Communication Protocol
- [ ] **Message Types**
  ```yaml
  task_assignment:    Orchestrator → Agent
  task_update:        Agent → Orchestrator (progress, completion)
  review_request:     Dev → Reviewer (via Orchestrator)
  review_feedback:    Reviewer → Dev (via Orchestrator)
  test_request:       Dev → QA (via Orchestrator)
  test_results:       QA → Dev (via Orchestrator)
  approval_request:   Agent → PM (via Orchestrator)
  approval_decision:  PM → Agent (via Orchestrator)
  ```

- [ ] **Workflow Orchestration**
  ```
  User Request
       │
       ▼
  ┌─────────┐    task breakdown    ┌─────────┐
  │   PM    │ ◄──────────────────► │  User   │  (checkpoint: approve plan)
  └────┬────┘                      └─────────┘
       │ creates tasks
       ▼
  ┌─────────┐    design review     ┌─────────┐
  │Architect│ ◄──────────────────► │  User   │  (checkpoint: approve design)
  └────┬────┘                      └─────────┘
       │ design complete
       ▼
  ┌─────────┐    implementation    ┌─────────┐
  │   Dev   │ ───────────────────► │Reviewer │  (automatic review)
  └────┬────┘                      └────┬────┘
       │                                │ feedback
       ◄────────────────────────────────┘
       │ code complete
       ▼
  ┌─────────┐    testing           ┌─────────┐
  │   QA    │ ───────────────────► │   Dev   │  (if tests fail)
  └────┬────┘                      └─────────┘
       │ tests pass
       ▼
  ┌─────────┐    acceptance        ┌─────────┐
  │   PM    │ ◄──────────────────► │  User   │  (checkpoint: optional)
  └─────────┘                      └─────────┘
  ```

#### 2.6 Git Branch Workflow
- [ ] **Branch Strategy**
  ```
  main                          # Protected, stable code
    │
    ├── working                 # Current working state
    │     │
    │     ├── agent/dev-1/task-001    # Dev agent 1's work
    │     ├── agent/dev-2/task-002    # Dev agent 2's work
    │     └── agent/qa/task-001-tests # QA agent's tests
    │
    └── checkpoints/
          ├── cp-001-architecture     # Checkpoint snapshots
          └── cp-002-feature-auth
  ```

- [ ] **Merge Process**
  ```python
  # Orchestrator merge logic (simplified)
  
  async def merge_agent_branch(agent_id: str, task_id: str):
      branch = f"agent/{agent_id}/{task_id}"
      
      # Attempt merge to working branch
      result = git.merge(branch, into="working")
      
      if result.has_conflicts:
          # Create checkpoint for human resolution
          checkpoint = create_checkpoint(
              type="merge_conflict",
              branches=[branch, "working"],
              conflicts=result.conflicts
          )
          await notify_user(checkpoint)
          # Block until resolved
          await wait_for_approval(checkpoint.id)
      
      # Clean up agent branch
      git.delete_branch(branch)
  ```

- [ ] **Conflict Resolution UI**
  ```bash
  $ team status
  
  ⚠️  MERGE CONFLICT in working branch
  
  Conflicting files:
    • src/auth/login.py (Dev-1 vs Dev-2)
  
  Options:
    team resolve --keep dev-1     # Keep Dev-1's version
    team resolve --keep dev-2     # Keep Dev-2's version
    team resolve --manual         # Open in editor
  ```

---

### Phase 3: Intelligence & Learning

**Goal**: Make the system smarter over time by learning from successes and failures.

#### 3.1 Memory Extraction Pipeline
- [ ] **Automatic Fact Extraction**
  ```python
  # After each task completion, extract learnings
  
  async def extract_task_learnings(task: Task, result: TaskResult):
      prompt = f"""
      Analyze this completed task and extract reusable knowledge:
      
      Task: {task.description}
      Files Modified: {result.files}
      Approach Taken: {result.summary}
      
      Extract:
      1. Architectural decisions made and why
      2. Coding patterns used
      3. Problems encountered and solutions
      4. Things that would help with similar tasks
      
      Format as structured facts.
      """
      
      facts = await llm.extract(prompt)
      
      for fact in facts:
          await mem0.add(
              fact.content,
              metadata={"task_id": task.id, "type": fact.type},
              namespace=f"project:{project_id}"
          )
  ```

- [ ] **Decision Recording**
  - Why was framework X chosen over Y?
  - Why this folder structure?
  - Why this error handling pattern?
  - Store with rationale for future reference

#### 3.2 Cross-Project Learning (Global Memory)
- [ ] **Preference Detection**
  ```python
  # Analyze patterns across projects
  
  async def detect_preferences():
      # Find repeated patterns
      patterns = await mem0.search(
          query="coding style preferences",
          namespace="project:*",  # All projects
          limit=100
      )
      
      # Cluster similar preferences
      clusters = cluster_similar(patterns)
      
      # Promote consistent preferences to global
      for cluster in clusters:
          if cluster.frequency > THRESHOLD:
              await mem0.add(
                  f"User preference: {cluster.summary}",
                  namespace="global:user"
              )
  ```

- [ ] **Preference Examples**
  ```yaml
  global_preferences:
    - "User prefers pytest over unittest"
    - "User likes type hints on all function signatures"
    - "User prefers composition over inheritance"
    - "User wants docstrings in Google style"
    - "User prefers FastAPI for APIs, Click for CLIs"
  ```

#### 3.3 Anti-Pattern Detection
- [ ] **Failure Analysis**
  ```python
  # Learn from rejected tasks and bugs
  
  async def analyze_failure(task: Task, rejection: Rejection):
      prompt = f"""
      This task was rejected:
      
      Task: {task.description}
      Code: {task.artifacts}
      Rejection reason: {rejection.reason}
      
      Extract anti-patterns to avoid in the future.
      """
      
      anti_patterns = await llm.extract(prompt)
      
      for pattern in anti_patterns:
          await mem0.add(
              f"AVOID: {pattern.description}",
              metadata={"type": "anti_pattern", "severity": pattern.severity},
              namespace=f"project:{project_id}"
          )
  ```

- [ ] **Anti-Pattern Examples**
  ```yaml
  anti_patterns:
    - "Don't use print() for logging in this project - use logger"
    - "Don't put business logic in route handlers"
    - "Don't use string formatting for SQL queries"
    - "Avoid deeply nested callbacks - use async/await"
  ```

#### 3.4 Contextual Memory Injection
- [ ] **Smart Retrieval**
  ```python
  # When agent starts a task, inject relevant memories
  
  async def prepare_agent_context(task: Task) -> str:
      # Get task-relevant memories
      memories = await mem0.search(
          query=task.description,
          namespaces=[
              f"project:{project_id}",  # Project-specific
              "global:user"              # User preferences
          ],
          limit=15
      )
      
      # Categorize for the prompt
      context = """
      ## Project Context
      {project_memories}
      
      ## Your Preferences
      {preference_memories}
      
      ## Things to Avoid
      {anti_patterns}
      """
      
      return context.format(...)
  ```

- [ ] **Memory Decay**
  - Old memories that are never retrieved get lower relevance scores
  - Contradicted memories are marked and eventually removed
  - User can manually forget: `team memory forget "bad pattern"`

#### 3.5 Learning Metrics Dashboard
- [ ] **Track Learning Effectiveness**
  ```bash
  $ team memory stats
  
  📊 Memory Statistics
  
  Project Memory: 147 facts
  ├── Architecture decisions: 23
  ├── Coding patterns: 45
  ├── Workarounds: 12
  └── Anti-patterns: 15
  
  Global Memory: 34 preferences
  
  Learning Metrics:
  ├── Memories retrieved this session: 89
  ├── Memories that improved output: 67 (75%)
  └── Memory retrieval avg latency: 45ms
  
  Most Used Memories:
  1. "Use repository pattern for data access" (used 23 times)
  2. "User prefers type hints" (used 19 times)
  3. "Auth uses JWT in httponly cookies" (used 15 times)
  ```

---

### Phase 4: Cost Optimization

**Goal**: Reduce LLM costs without sacrificing quality.

#### 4.1 Ollama Integration
- [ ] **Local Model Setup**
  ```yaml
  # docker-compose.yaml addition
  
  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama-data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
  ```

- [ ] **Model Pull on Startup**
  ```bash
  # Pull recommended models
  ollama pull codellama:13b      # For coding tasks
  ollama pull llama3.2           # For general tasks
  ollama pull nomic-embed-text   # For embeddings (replace OpenAI)
  ```

#### 4.2 Intelligent Model Routing
- [ ] **Task Complexity Scoring**
  ```python
  def score_task_complexity(task: Task) -> str:
      """Returns: 'simple', 'medium', 'complex'"""
      
      signals = {
          "simple": [
              "fix typo", "rename", "add comment",
              "update version", "simple refactor"
          ],
          "complex": [
              "security", "architecture", "design",
              "performance", "complex algorithm"
          ]
      }
      
      # Check keywords, file count, dependency depth
      # Return appropriate tier
  ```

- [ ] **Model Selection Logic**
  ```python
  async def select_model(task: Task, agent_role: str) -> Model:
      complexity = score_task_complexity(task)
      
      routing_rules = {
          ("dev", "simple"): "ollama/codellama:13b",
          ("dev", "medium"): "ollama/codellama:13b",  # Try local first
          ("dev", "complex"): "claude-code",
          
          ("qa", "simple"): "ollama/codellama:13b",
          ("qa", "medium"): "ollama/codellama:13b",
          ("qa", "complex"): "claude-code",
          
          ("reviewer", "simple"): "ollama/llama3.2",
          ("reviewer", "medium"): "claude-code",
          ("reviewer", "complex"): "claude-code",
          
          ("pm", "*"): "claude-code",           # Always smart model
          ("architect", "*"): "claude-code",    # Always smart model
      }
      
      return routing_rules.get((agent_role, complexity))
  ```

- [ ] **Fallback on Failure**
  ```python
  async def execute_with_fallback(task: Task, agent: Agent):
      primary_model = await select_model(task, agent.role)
      
      result = await execute(task, primary_model)
      
      if not result.success and primary_model.is_local:
          # Retry with Claude if local model failed
          logger.info(f"Local model failed, falling back to Claude")
          result = await execute(task, Model("claude-code"))
      
      return result
  ```

#### 4.3 Cost Tracking
- [ ] **Token Counting**
  ```python
  @dataclass
  class CostRecord:
      task_id: str
      model: str
      input_tokens: int
      output_tokens: int
      cost_usd: float
      timestamp: datetime
  
  # Track in Redis
  async def record_cost(record: CostRecord):
      await redis.lpush(f"costs:{project_id}", record.to_json())
      await redis.incrbyfloat(f"costs:{project_id}:total", record.cost_usd)
  ```

- [ ] **Cost Dashboard**
  ```bash
  $ team cost
  
  💰 Cost Report - my-api project
  
  This Session:
  ├── Claude Code: $1.23 (45 calls)
  ├── Claude API: $0.15 (3 calls)
  └── Local Models: $0.00 (127 calls)
  ───────────────────────────────
  Total: $1.38
  
  Savings from local models: $2.45 (64%)
  Savings from memory: $0.89 (reduced context)
  
  Cost by Agent:
  ├── Dev: $0.98
  ├── QA: $0.12 (mostly local)
  ├── Reviewer: $0.23
  └── PM: $0.05
  
  Projected monthly cost at this rate: $45
  ```

#### 4.4 Semantic Caching
- [ ] **Cache Common Operations**
  ```python
  # Cache responses for similar prompts
  
  async def cached_llm_call(prompt: str, **kwargs) -> str:
      # Generate embedding for prompt
      embedding = await embed(prompt)
      
      # Check for similar cached prompts
      cached = await redis.vector_search(
          index="prompt_cache",
          vector=embedding,
          threshold=0.95  # Very similar
      )
      
      if cached:
          logger.info(f"Cache hit! Saved {cached.tokens} tokens")
          return cached.response
      
      # No cache hit, make actual call
      response = await llm.generate(prompt, **kwargs)
      
      # Cache the response
      await redis.vector_add(
          index="prompt_cache",
          vector=embedding,
          data={"prompt": prompt, "response": response}
      )
      
      return response
  ```

- [ ] **Cache Invalidation**
  - Cache entries expire after 24 hours
  - Invalidate when relevant files change
  - User can clear: `team cache clear`

#### 4.5 Batch Processing
- [ ] **Group Similar Tasks**
  ```python
  # When multiple similar tasks are queued, batch them
  
  async def batch_similar_tasks(tasks: List[Task]) -> List[TaskGroup]:
      # Group by type and affected files
      groups = defaultdict(list)
      
      for task in tasks:
          key = (task.type, frozenset(task.affected_files))
          groups[key].append(task)
      
      # Create batched prompts
      batched = []
      for key, task_list in groups.items():
          if len(task_list) > 1:
              batched.append(TaskGroup(tasks=task_list, batched=True))
          else:
              batched.append(TaskGroup(tasks=task_list, batched=False))
      
      return batched
  ```

---

### Phase 5: Polish & Extensions

**Goal**: Production-ready UX and extensibility.

#### 5.1 Rich TUI Dashboard
- [ ] **Live Status View** (using Textual or Rich)
  ```
  ┌─────────────────────────────────────────────────────────────────────┐
  │ AGENT TEAM - my-api                                    ⏱ 00:23:45  │
  ├─────────────────────────────────────────────────────────────────────┤
  │ AGENTS                          │ TASKS                             │
  │ ┌─────────────────────────────┐ │ ┌───────────────────────────────┐ │
  │ │ 🟢 PM        idle           │ │ │ ✅ Setup project structure   │ │
  │ │ 🟢 Architect idle           │ │ │ ✅ Design auth system        │ │
  │ │ 🔵 Dev-1    implementing    │ │ │ 🔄 Implement user model      │ │
  │ │ 🔵 Dev-2    implementing    │ │ │ ⏳ Implement login endpoint  │ │
  │ │ 🟡 QA       waiting         │ │ │ ⏳ Write auth tests          │ │
  │ │ 🟢 Reviewer idle           │ │ │ ⏳ ... 4 more                 │ │
  │ └─────────────────────────────┘ │ └───────────────────────────────┘ │
  ├─────────────────────────────────┴───────────────────────────────────┤
  │ ACTIVITY LOG                                                        │
  │ 14:23:01 Dev-1  Created src/models/user.py                         │
  │ 14:23:15 Dev-1  Created src/models/base.py                         │
  │ 14:23:32 Dev-2  Working on src/auth/jwt.py                         │
  │ 14:23:45 QA     Waiting for testable code...                       │
  ├─────────────────────────────────────────────────────────────────────┤
  │ 💰 $0.45 this session │ 📊 5/12 tasks │ ⚡ 2 agents active         │
  └─────────────────────────────────────────────────────────────────────┘
  │ [S]tatus [L]ogs [A]pprove [P]ause [C]hat [Q]uit                    │
  └─────────────────────────────────────────────────────────────────────┘
  ```

- [ ] **Interactive Elements**
  - Click on agent to see details
  - Click on task to see progress
  - Keyboard shortcuts for common actions
  - Real-time log streaming

#### 5.2 Project Templates
- [ ] **Built-in Templates**
  ```bash
  $ team init --template fastapi
  
  Available templates:
  ├── fastapi      - REST API with FastAPI, SQLAlchemy, JWT auth
  ├── flask        - Flask with Blueprints, SQLAlchemy
  ├── django       - Django with DRF, standard project layout
  ├── cli          - Click-based CLI tool with tests
  ├── library      - Python package with pyproject.toml, docs
  ├── react        - React + TypeScript + Vite
  ├── nextjs       - Next.js with App Router
  └── fullstack    - FastAPI backend + React frontend
  ```

- [ ] **Template Contents**
  ```yaml
  # templates/fastapi/template.yaml
  
  name: FastAPI REST API
  description: Production-ready API with auth, database, tests
  
  memory_seed:
    - "This project uses FastAPI with async SQLAlchemy"
    - "Auth is JWT-based with refresh tokens"
    - "Use Pydantic models for request/response validation"
    - "Tests use pytest with async fixtures"
    - "Database migrations use Alembic"
  
  initial_structure:
    - src/
    - src/api/
    - src/models/
    - src/services/
    - tests/
    - alembic/
    - docker-compose.yaml
    - pyproject.toml
  
  default_tasks:
    - "Set up project structure and dependencies"
    - "Configure database connection and models"
    - "Implement health check endpoint"
  ```

#### 5.3 Custom Agent Definitions
- [ ] **User-Defined Agents**
  ```yaml
  # .agent-team/agents/security-expert.yaml
  
  agent:
    name: "Security Expert"
    id: "security"
    
    model:
      primary: "claude-code"
      
    capabilities:
      - "read_files"
      - "run_security_scans"
      - "create_issues"
      
    system_prompt: |
      You are a Security Expert agent specializing in:
      - OWASP Top 10 vulnerabilities
      - Authentication/authorization flaws
      - Input validation and sanitization
      - Secure coding practices
      
      Review all code for security issues. Be thorough but
      avoid false positives. Prioritize findings by severity.
      
    triggers:
      - on: "task_complete"
        when: "task.type in ['auth', 'api', 'database']"
        action: "review"
  ```

- [ ] **Agent Marketplace** (future)
  ```bash
  $ team agent search "kubernetes"
  
  Available agents:
  ├── k8s-deployer   - Generates K8s manifests and Helm charts
  ├── k8s-debugger   - Diagnoses cluster issues
  └── k8s-optimizer  - Suggests resource optimizations
  
  $ team agent install k8s-deployer
  ```

#### 5.4 Plugin System
- [ ] **Plugin Architecture**
  ```python
  # plugins/my_plugin.py
  
  from agent_team import Plugin, hook
  
  class MyPlugin(Plugin):
      name = "my-plugin"
      version = "1.0.0"
      
      @hook("task.before_start")
      async def before_task(self, task: Task):
          """Run before any task starts"""
          # Add custom logic
          pass
      
      @hook("task.after_complete")
      async def after_task(self, task: Task, result: Result):
          """Run after task completes"""
          # Send notification, update external system, etc.
          pass
      
      @hook("agent.before_spawn")
      async def customize_agent(self, agent: Agent, task: Task):
          """Modify agent before it runs"""
          # Inject additional context
          agent.context += self.get_custom_context()
  ```

- [ ] **Example Plugins**
  ```
  plugins/
  ├── slack-notifier     - Send updates to Slack
  ├── github-integration - Create PRs, update issues
  ├── jira-sync          - Sync tasks with Jira
  ├── datadog-metrics    - Send metrics to Datadog
  └── custom-linter      - Run custom linting rules
  ```

#### 5.5 Configuration Export/Import
- [ ] **Export Configuration**
  ```bash
  $ team export my-team-config.yaml
  
  Exported:
  ├── Agent configurations
  ├── Checkpoint rules
  ├── Model routing preferences
  ├── Global memory (preferences only)
  └── Plugin settings
  
  $ team export --include-project-memory project-config.yaml
  # Also includes project-specific memory
  ```

- [ ] **Import Configuration**
  ```bash
  $ team import my-team-config.yaml
  
  This will:
  ├── Update agent configurations
  ├── Apply checkpoint rules
  └── Import 34 global preferences
  
  Proceed? [y/N]
  ```

- [ ] **Team Sharing**
  ```bash
  # Share your optimized setup with teammates
  $ team export --public my-awesome-config.yaml
  
  # Upload to community registry (future)
  $ team publish my-awesome-config
  ```

#### 5.6 Advanced CLI Features
- [ ] **Spec File Input**
  ```bash
  # Start from a detailed spec file
  $ team start --spec requirements.md
  
  # Spec file format
  $ cat requirements.md
  # Project: Todo API
  
  ## Requirements
  - User authentication with JWT
  - CRUD operations for todos
  - Due dates and reminders
  
  ## Technical Constraints
  - Use FastAPI
  - PostgreSQL database
  - Deploy to Docker
  ```

- [ ] **Dry Run Mode**
  ```bash
  $ team start "Add user profile feature" --dry-run
  
  🔍 Dry Run - No changes will be made
  
  PM would create tasks:
  ├── Task 1: Design user profile schema
  ├── Task 2: Create profile model and migration
  ├── Task 3: Implement GET /users/{id}/profile
  ├── Task 4: Implement PUT /users/{id}/profile
  └── Task 5: Add profile tests
  
  Estimated:
  ├── Time: 45-60 minutes
  ├── Cost: $0.80-1.20
  └── Files: ~8 new, ~3 modified
  
  Run without --dry-run to execute.
  ```

- [ ] **Resume & Recovery**
  ```bash
  # If system crashes or is stopped
  $ team resume
  
  Found interrupted session from 2 hours ago:
  ├── Project: my-api
  ├── Progress: 7/12 tasks complete
  ├── Active: Task 8 (implementing login)
  └── Last checkpoint: cp-003-auth-design
  
  Options:
  [R]esume from last state
  [C]heckpoint - resume from cp-003
  [A]bort and start fresh
  ```

---

## Design Decisions (Finalized)

1. **Agent Spawning Strategy**: **Per-task ephemeral containers**
   - Each task spawns a fresh container, torn down on completion
   - Clean state eliminates cross-task contamination
   - Mem0 provides continuity between tasks (no need for long-running state)
   - Tradeoff: ~2-3s startup overhead per task (acceptable)

2. **Claude Code Integration**: **Subprocess with `--dangerously-skip-permissions`**
   - Full autonomy since we're already sandboxed in Docker
   - Wrapper captures stdout/stderr and parses structured output
   - Agent container includes Claude Code CLI pre-installed

3. **Multi-Agent Coordination**: **Git branches per agent**
   - Each agent works on its own branch (e.g., `agent/dev-1/task-001`)
   - Orchestrator handles merging back to main working branch
   - Merge conflicts trigger a checkpoint for human review
   - Future: Could add automated conflict resolution for simple cases

4. **Rollback Granularity**: **Per-checkpoint with git history**
   - Each checkpoint creates a git tag
   - `team rollback <checkpoint>` resets to that state
   - Full git history preserved for manual recovery if needed

5. **Conflict Resolution**: **Default to human via checkpoint**
   - When agents disagree (e.g., Dev vs QA on implementation)
   - Orchestrator creates a checkpoint with both perspectives
   - User makes final decision
   - Future: Could add voting or seniority rules as options

---

## Next Steps

### Immediate: Build Phase 1 Prototype

1. **Set up project structure**
   ```
   agent-team/
   ├── cli/                    # Python CLI (Click or Typer)
   ├── orchestrator/           # FastAPI service
   ├── agent/                  # Agent wrapper and execution logic
   ├── containers/
   │   ├── orchestrator/       # Dockerfile + requirements
   │   ├── agent/              # Dockerfile + Claude Code setup
   │   └── mem0/               # Dockerfile (thin wrapper)
   ├── config/
   │   └── default.yaml        # Default configuration
   ├── docker-compose.yaml
   ├── Makefile                # Convenience commands
   └── README.md
   ```

2. **Build core components**
   - CLI with `init`, `start`, `status`, `approve`, `logs` commands
   - Orchestrator with task management and agent spawning
   - Agent container with Claude Code + memory integration
   - Mem0 configuration with Redis backend

3. **Test the flow**
   - `team init` in a new directory
   - `team start "Create a Python hello world script"`
   - Watch the Dev agent execute
   - Verify memory is saved
   - Run another task, verify memory is retrieved

### Future: Phase 2+

After Phase 1 is stable:
- Add PM agent for task breakdown
- Add QA agent for testing
- Implement git branch workflow
- Add more sophisticated checkpoints
