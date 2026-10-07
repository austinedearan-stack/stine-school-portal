import re

from django.core.exceptions import ValidationError


class ComplexPasswordValidator:
    """
    Enforces password complexity:
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special symbol
    """
    def validate(self, password, user=None):
        if not re.search(r'[A-Z]', password):
            raise ValidationError("Password must contain at least one uppercase letter (A-Z).")
        if not re.search(r'[a-z]', password):
            raise ValidationError("Password must contain at least one lowercase letter (a-z).")
        if not re.search(r'[0-9]', password):
            raise ValidationError("Password must contain at least one digit (0-9).")
        if not re.search(r'[^A-Za-z0-9]', password):
            raise ValidationError("Password must contain at least one special character (!@#$%^&* etc.).")

    def get_help_text(self):
        return "Your password must contain at least one uppercase letter, one lowercase letter, one digit, and one special character."


class MaximumLengthValidator:
    """Caps password length so attackers cannot submit megabyte passwords to burn hashing CPU."""

    def __init__(self, max_length=128):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(f"Password must be at most {self.max_length} characters long.")

    def get_help_text(self):
        return f"Your password must be at most {self.max_length} characters long."
