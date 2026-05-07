from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('contests/', include('apps.contests.urls')),
    path('submissions/', include('apps.submissions.urls')),
    path('leaderboard/', include('apps.leaderboard.urls')),

    # Root → redirect to contest list
    path('', RedirectView.as_view(url='/contests/', permanent=False)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)