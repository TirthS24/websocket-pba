"""
Django app configuration for ws_server project.
Handles graph initialization on startup.
"""

import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class WsServerConfig(AppConfig):
    """App configuration for ws_server project."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ws_server"

    def ready(self):
        """Set up appdata path on Django startup.
        
        Note: Graph initialization now happens lazily during session creation
        to avoid event loop issues at startup.
        """
        # Only set up if not in a migration or management command
        import sys
        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            return

        try:
            import os
            from pathlib import Path
            from django.conf import settings

            # Set APPDATA_FOLDER_PATH if not already set in environment
            if not os.environ.get("APPDATA_FOLDER_PATH"):
                # Default to ws_server/appdata relative to BASE_DIR
                appdata_path = getattr(settings, "APPDATA_FOLDER_PATH", None)
                if not appdata_path:
                    appdata_path = str(Path(settings.BASE_DIR) / "appdata")
                os.environ["APPDATA_FOLDER_PATH"] = appdata_path
                logger.info(f"APPDATA_FOLDER_PATH set to: {appdata_path}")
        except Exception as e:
            logger.error(f"Failed to set up appdata path: {e}", exc_info=True)
