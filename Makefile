.PHONY: build up down logs status agent-image clean dev install test lint format help

WORKSPACE ?= $(CURDIR)/workspace

# === Docker Commands ===

build: agent-image ## Build all Docker images
	docker compose build

agent-image: ## Build the agent Docker image
	docker build -t legatus-agent:latest -f containers/agent/Dockerfile .

up: agent-image ## Start all services
	mkdir -p $(WORKSPACE)
	WORKSPACE=$(WORKSPACE) docker compose up -d --build

down: ## Stop all services
	docker compose down

logs: ## View service logs
	docker compose logs -f

restart: ## Restart all services
	docker compose restart

ps: ## Show service status
	docker compose ps

clean: ## Stop services and remove volumes
	docker compose down -v
	docker rmi legatus-agent:latest 2>/dev/null || true

# === Development Commands ===

install: ## Install project locally with all extras
	uv venv
	uv pip install -e ".[orchestrator,agent,dev]"

dev: ## Run orchestrator locally (requires Redis + Mem0)
	uv run legatus-orchestrator

test: ## Run tests
	uv run pytest

lint: ## Run linter
	uv run ruff check src/

format: ## Format code
	uv run ruff format src/

# === Help ===

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
