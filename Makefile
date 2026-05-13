.PHONY: test lint typecheck run-chaos report clean docker-up docker-down

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check src tests scripts

typecheck:
	python3 -m mypy src

run-chaos:
	python3 scripts/run_chaos.py --config configs/default.yaml --out reports/metrics.json

report:
	python3 scripts/generate_report.py --metrics reports/metrics.json --out reports/final_report.md

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/metrics.json reports/final_report.md
