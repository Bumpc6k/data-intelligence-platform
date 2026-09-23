.PHONY: help venv test smoke lint gate-fast gate clean

help:
	@grep -E '^[a-z-]+:' Makefile | sed 's/:.*//' | sed 's/^/  make /'

venv:
	python3 -m venv .venv && .venv/bin/pip install -q -U pip \
	 && .venv/bin/pip install -q -e ".[api,dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple

test:
	.venv/bin/python -m pytest

smoke:
	.venv/bin/python -m pytest -m smoke -rs

lint:
	.venv/bin/ruff check .

gate-fast: lint test

gate: lint test smoke

clean:
	rm -rf .pytest_cache .ruff_cache */**/__pycache__ **/__pycache__ 2>/dev/null || true
