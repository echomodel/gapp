VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

.PHONY: setup
setup:
	python3 -m venv $(VENV)
	$(PIP) install --index-url https://pypi.org/simple --upgrade pip
	$(PIP) install --index-url https://pypi.org/simple -e .
	$(PIP) install --index-url https://pypi.org/simple pytest pytest-asyncio pyjwt httpx pyyaml pydantic starlette

.PHONY: test
test: setup
	$(PYTHON) -m pytest tests/

.PHONY: install
install:
	pipx install -e . --pip-args="--index-url https://pypi.org/simple" --force

.PHONY: clean
clean:
	rm -rf $(VENV)
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
