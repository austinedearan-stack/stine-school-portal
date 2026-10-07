# Deployment Guide

## 1. Production Deployment Topology
The production deployment uses Docker containers managed via Docker Compose:
- **Web App**: Gunicorn WSGI server behind an Nginx reverse proxy.
- **Database**: PostgreSQL 16+ with dedicated, non-superuser credentials.
- **Cache & Throttling**: Redis 7+.

## 2. Docker Compose Configuration
See [`docker-compose.yml`](file:///c:/Users/deara/OneDrive/Documents/projects/school%20portal/docker-compose.yml) and [`Dockerfile`](file:///c:/Users/deara/OneDrive/Documents/projects/school%20portal/Dockerfile).

### Build and Launch:
```bash
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
```

## 3. Environment Variables (Production)
Set the following in `.env`:
- `ENVIRONMENT=production`
- `SECRET_KEY=<generate-at-least-50-characters-random-string>`
- `DEBUG=False`
- `ALLOWED_HOSTS=portal.university.edu`
- `DB_ENGINE=postgresql`
- `DB_NAME=university_portal`
- `DB_USER=portal_app_user`
- `DB_PASSWORD=<strong_password>`
- `DB_HOST=db`
- `DB_PORT=5432`
- `SECURE_SSL_REDIRECT=True`
- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`

## 4. PostgreSQL Hardening
- Create a dedicated non-superuser role:
```sql
CREATE USER portal_app_user WITH PASSWORD 'strong_password';
CREATE DATABASE university_portal OWNER portal_app_user;
REVOKE ALL ON DATABASE university_portal FROM PUBLIC;
GRANT ALL PRIVILEGES ON DATABASE university_portal TO portal_app_user;
```
