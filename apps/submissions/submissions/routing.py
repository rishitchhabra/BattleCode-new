from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r'ws/submission/(?P<submission_id>\d+)/$',
        consumers.SubmissionConsumer.as_asgi()
    ),
]