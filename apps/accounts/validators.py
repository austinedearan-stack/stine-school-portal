"""Password validators. Composition rules (upper/lower/digit/symbol) are deliberately NOT enforced:
NIST SP 800-63B discourages them; length, breached/common-password and similarity checks are used instead
(ARCHITECTURE.md §4.4)."""

from django.core.exceptions import ValidationError


class MaximumLengthValidator:
    """Caps password length so attackers cannot submit megabyte passwords to burn hashing CPU."""

    def __init__(self, max_length=128):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(f"Password must be at most {self.max_length} characters long.")

    def get_help_text(self):
        return f"Your password must be at most {self.max_length} characters long."
