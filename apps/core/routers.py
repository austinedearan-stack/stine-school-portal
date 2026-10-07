"""Database routing: the ``audit`` alias is only a second connection to the default database."""


class AuditRouter:
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "default"
