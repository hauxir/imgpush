.PHONY: install install-dev lint format typecheck check clean

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-dev.txt

lint:
	ruff check app/

format:
	ruff format app/

fix:
	ruff check --fix app/

typecheck:
	basedpyright app/

check: lint typecheck

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete