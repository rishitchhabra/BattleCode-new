import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'coding_platform.settings')

django_asgi_app = get_asgi_application()

from apps.submissions.routing import websocket_urlpatterns as sub_ws
from apps.leaderboard.routing import websocket_urlpatterns as lb_ws

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(sub_ws + lb_ws)
    ),
})