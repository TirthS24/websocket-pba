"""
URL configuration for ws_server project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from .health import health
from ws_server.realtime import views
from ws_server.realtime.csrf_handler import get_csrf_token_view

urlpatterns = [
    path('admin/', admin.site.urls),
    # REQUIRED: health check endpoint for ALB target group
    path("health/", health),
    # CSRF token endpoint
    path("api/csrf-token/", get_csrf_token_view, name="csrf_token"),
    # Thread endpoints
    path("api/thread/summarize", views.summarize_thread_view, name="summarize"),
    path("api/thread/history", views.thread_history_view, name="history"),
    path("api/chat/sms", views.sms_chat_view, name="sms_chat"),
]
