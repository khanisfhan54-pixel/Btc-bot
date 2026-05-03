# FIX-5.6: ARE smoke test harness for CI.
.PHONY: smoke test

smoke:
	python -m pytest -k smoke --tb=short -q

test:
	python -m pytest --tb=short -q
