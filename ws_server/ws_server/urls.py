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

from realtime.views import thread_connect, thread_history, thread_summarize, chat_sms, websocket_test_page
from .health import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # REQUIRED: health check endpoint for ALB target group
    path("health/", health),
    # WebSocket + LLM + two users test page (vanilla HTML/CSS/JS)
    path("test-ws/", websocket_test_page),
    # FE sends thread_id; ws_server triggers LLM to open WebSocket for that thread
    path("api/thread/connect/", thread_connect),
    # Proxy to LLM (LLM not exposed; all traffic via ws_server)
    path("api/thread/summarize/", thread_summarize),
    path("api/thread/history/", thread_history),
    path("api/chat/sms/", chat_sms),
]
