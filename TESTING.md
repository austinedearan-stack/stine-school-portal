# Comprehensive Testing Strategy

## 1. Test Architecture
The test suite is structured to cover:
- **Unit Tests**: Models, validators, services, permission checks.
- **Integration Tests**: Multi-step workflows (login -> MFA -> registration -> booking).
- **Authorization & RBAC Tests**: Explicit verification that unauthorized roles cannot invoke protected actions.
- **Security Tests**: SQL injection, stored XSS, CSRF, IDOR / BOLA, path traversal file uploads, brute force lockout.
- **Concurrency & Race Condition Tests**: Multi-threaded execution attempting to reserve the last remaining hostel bed and unit seat simultaneously.

## 2. Running Tests
To run all tests:
```bash
pytest -v
```

To run security and authorization tests specifically:
```bash
pytest -k "security or rbac or idor or concurrency"
```

To run with coverage:
```bash
pytest --cov=apps --cov-report=term-missing
```
