"""Separate administration application mounted beside the development app."""
from flask import Flask
from flask_wtf.csrf import CSRFProtect, CSRFError
from onpf.db import close_db
from onpf.errors import DomainError, handle_domain_error


def create_admin_app(parent):
    app = Flask('onpf.administration', template_folder='../templates')
    app.config.update(parent.config)
    CSRFProtect(app)
    app.teardown_appcontext(close_db)
    app.register_error_handler(DomainError, handle_domain_error)
    app.register_error_handler(CSRFError, lambda _: ('The form expired. Reload administration.',400))
    from onpf.administration.routes import bp
    app.register_blueprint(bp)
    return app
