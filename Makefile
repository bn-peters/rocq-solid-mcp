PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest
PYTEST_WORKERS ?= auto
PYTEST_PARALLEL := -n $(PYTEST_WORKERS)

.PHONY: test unit integration e2e promote clean

test:
	$(PYTEST) -q $(PYTEST_PARALLEL) tests/unit tests/integration tests/e2e

unit:
	$(PYTEST) -q $(PYTEST_PARALLEL) tests/unit

integration:
	$(PYTEST) -q $(PYTEST_PARALLEL) tests/integration

e2e:
	$(PYTEST) -q $(PYTEST_PARALLEL) tests/e2e

promote:
	$(PYTEST) -q tests/e2e --promote

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name "*.egg-info" \) -prune -exec rm -rf {} +
