# Shortcuts for every OS; on Windows, make comes from `winget install ezwinports.make`, Chocolatey or Scoop.
.DEFAULT_GOAL := help
.PHONY: help install uninstall test lint format typecheck check

INTERVAL_SECONDS ?= 300
UV := uv run --group dev

ifeq ($(OS),Windows_NT)
  INSTALL := powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -IntervalSeconds $(INTERVAL_SECONDS)
  UNINSTALL := powershell -NoProfile -ExecutionPolicy Bypass -File scripts/uninstall.ps1
else
  INSTALL := INTERVAL_SECONDS=$(INTERVAL_SECONDS) scripts/install.sh
  UNINSTALL := scripts/uninstall.sh
endif

help:
	@echo make install: Install the scheduled job, every INTERVAL_SECONDS, default 300
	@echo make uninstall: Remove the scheduled job
	@echo make test: Run the tests
	@echo make lint: Check linting and formatting
	@echo make format: Fix linting and formatting
	@echo make typecheck: Type-check for macOS, Linux and Windows
	@echo make check: Run every check

install:
	@$(INSTALL)

uninstall:
	@$(UNINSTALL)

test:
	$(UV) pytest

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

format:
	$(UV) ruff check --fix .
	$(UV) ruff format .

typecheck:
	$(UV) mypy --platform darwin
	$(UV) mypy --platform linux
	$(UV) mypy --platform win32

check: lint typecheck test
