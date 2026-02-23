"""
WSGI config for ws_server project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""
# Load secrets from AWS Secrets Manager before Django settings are loaded
import ws_server.env_bootstrap  # noqa: F401

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ws_server.settings')

application = get_wsgi_application()
