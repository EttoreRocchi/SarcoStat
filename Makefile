.DEFAULT_GOAL := help
.PHONY: help install lint format format-check pre-commit \
        test clean publish publish-test smoke

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

lint:  ## Run ruff linter
	@ruff check --fix $(SRC)/ $(TESTS)/

format:  ## Run ruff formatter (applies changes)
	@ruff format $(SRC)/ $(TESTS)/

format-check:  ## Run ruff formatter in check-only mode (no changes)
	@ruff format --check $(SRC)/ $(TESTS)/

pre-commit:  ## Run the full pre-commit suite on all files
	@pre-commit run --all-files

# Tests

test:  ## Run tests
	@pytest $(TESTS)/

smoke:  ## Quick end-to-end smoke run for the simulation pipelines
	@sarcostat sim sarc --config examples/configs/config_sarc_minimal.yaml

# Housekeeping

clean:  ## Remove build artefacts and caches
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info"   -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist/ build/ .ruff_cache/

# Release

publish: clean  ## Build and publish package to PyPI
	@read -p "Publish to PyPI? [y/N] " ans && [ "$$ans" = "y" ]
	@python -m build
	@twine upload dist/*

publish-test: clean  ## Build and publish package to TestPyPI
	@python -m build
	@twine upload --repository testpypi dist/*
