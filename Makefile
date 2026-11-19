.DEFAULT_GOAL := help
.PHONY: help install lint lint-fix format format-check typecheck check pre-commit \
        test clean build-check publish publish-test smoke

# Colours
BOLD  := \033[1m
RESET := \033[0m
CYAN  := \033[36m

# Paths
SRC   := src/sarcostat
TESTS := tests

help:  ## Show this help message
	@printf "$(BOLD)SarcoStat - available targets$(RESET)\n\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2}'

# Installation

install:  ## Install package with development dependencies
	@pip install -e ".[dev]"

# Code quality

lint:  ## Run ruff linter (check only, same scope as CI)
	@python -m ruff check .

lint-fix:  ## Run ruff linter and apply safe fixes
	@python -m ruff check --fix .

format:  ## Run ruff formatter (applies changes)
	@python -m ruff format .

format-check:  ## Run ruff formatter in check-only mode (no changes)
	@python -m ruff format --check .

typecheck:  ## Run mypy on the package (blocking in CI)
	@python -m mypy $(SRC)

check: lint format-check typecheck test  ## Run every check CI runs

pre-commit:  ## Run the full pre-commit suite on all files
	@pre-commit run --all-files

# Tests

test:  ## Run tests
	@python -m pytest $(TESTS)/

smoke:  ## Quick end-to-end smoke run for the simulation pipelines
	@sarcostat sim sarc --config examples/configs/config_sarc_minimal.yaml

# Housekeeping

clean:  ## Remove build artefacts and caches
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info"   -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist/ build/ .ruff_cache/

# Release

build-check: clean  ## Build sdist and wheel and validate their metadata
	@python -m build
	@twine check --strict dist/*

publish: build-check  ## Build and publish package to PyPI
	@read -p "Publish to PyPI? [y/N] " ans && [ "$$ans" = "y" ]
	@twine upload dist/*

publish-test: build-check  ## Build and publish package to TestPyPI
	@twine upload --repository testpypi dist/*
