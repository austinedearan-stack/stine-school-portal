.PHONY: install test test-pg lint security scan check-deploy run
install:      ; pip install -r requirements/dev.txt
test:         ; pytest
test-pg:      ; DB_ENGINE=postgresql pytest
lint:         ; ruff check .
security:     ; bandit -q -ll -c pyproject.toml -r apps portal_config && pip-audit -r requirements/prod.txt
scan:         ; python scripts/secret_scan.py
run:          ; python manage.py runserver
