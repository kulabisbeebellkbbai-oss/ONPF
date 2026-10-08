"""Errors safe to show in a browser; entered fields remain available."""
from flask import render_template, request


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def handle_domain_error(error: DomainError):
    # Passwords and tokens are never reflected into validation pages.
    values = {key: value for key, value in request.form.items()
              if "password" not in key.lower() and "token" not in key.lower()}
    return render_template("error.html", error=error, submitted=values), error.status
