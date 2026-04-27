PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest

.PHONY: test clean

test:
	RUN_ROCQ_LSP_INTEGRATION=1 RUN_ROCQ_LSP_E2E=1 $(PYTEST) -q

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name "*.egg-info" \) -prune -exec rm -rf {} +
