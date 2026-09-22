.DEFAULT_GOAL := help
COMPOSE := docker compose
PLATFORM := services/platform

.PHONY: help up down logs seed verify reset test test-local lint ui-build agent-matrix backtest clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Start the whole stack (migrations, seed, broker, simulator, evaluator, API, UI)
	$(COMPOSE) up --build

down: ## Stop the stack and remove volumes
	$(COMPOSE) down -v

logs: ## Tail service logs
	$(COMPOSE) logs -f --tail=100

seed: ## Re-run migrations, ontology load and rule install
	$(COMPOSE) exec api afdd seed

verify: ## Check inventory counts and print detected issues and data-quality outcomes
	$(COMPOSE) exec api afdd verify

reset: ## Clear telemetry, issues and agent state (keeps ontology and rules)
	$(COMPOSE) exec api afdd db reset

agent-matrix: ## Run the repeatable AI rule-authoring case matrix
	$(COMPOSE) exec api afdd agent evaluate

backtest: ## Backtest the shipped rule over the full source window
	$(COMPOSE) exec api afdd backtest --rule ahu-supply-air-deviation \
	  --start 2026-01-15T08:00:00 --end 2026-01-15T14:00:00 --label "full source window"

test: ## Run the Python test suite inside the container
	$(COMPOSE) run --rm --no-deps -e SOURCE_DIR_TEST=/srv/source-pack \
	  -v $(PWD)/$(PLATFORM)/tests:/srv/app/tests api pytest -q

test-local: ## Run the Python test suite on the host (no database or broker needed)
	cd $(PLATFORM) && python -m pytest -q

lint: ## Lint the Python package
	cd $(PLATFORM) && python -m ruff check afdd

ui-build: ## Type-check and build the dashboard
	cd services/ui && npm install --no-audit --no-fund && npm run build

clean: ## Remove local build artefacts
	rm -rf services/ui/dist services/ui/node_modules $(PLATFORM)/.pytest_cache
	find $(PLATFORM) -name __pycache__ -type d -prune -exec rm -rf {} +
