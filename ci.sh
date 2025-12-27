#!/bin/bash
set -e

echo "Running basedpyright type checks..."
basedpyright app/

echo ""
echo "Running ruff linting..."
ruff check app/

echo ""
echo "Running tests..."
cd app && python -m pytest tests/ -v

echo ""
echo "All checks passed!"
