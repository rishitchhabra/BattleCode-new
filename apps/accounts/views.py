from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import RegisterForm, LoginForm
from .models import User


def register_view(request):
    if request.user.is_authenticated:
        return redirect('contest_list')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.username}!')
            return redirect('contest_list')
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('contest_list')
    kicked_param = request.GET.get('kicked') == '1'
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            # ── Single-session lock: block if participant was kicked ──
            from apps.contests.models import ContestParticipant
            active_kick = ContestParticipant.objects.filter(
                user=user, kicked_at__isnull=False
            ).first()
            if active_kick:
                return render(request, 'accounts/login.html', {
                    'form': form,
                    'kicked': True,
                    'show_rejoin': True,
                    'kick_blocked_msg': (
                        f'You were signed out of "{active_kick.contest.title}". '
                        'Normal login is blocked. Please use your Rejoin Code from the administrator.'
                    )
                })
            login(request, user)
            next_url = request.GET.get('next', 'contest_list')
            return redirect(next_url)
        messages.error(request, 'Invalid username or password.')
    else:
        form = LoginForm()
    return render(request, 'accounts/login.html', {'form': form, 'kicked': kicked_param})


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def profile_view(request):
    submissions = request.user.submissions.select_related('question').all()[:20]
    return render(request, 'accounts/profile.html', {'submissions': submissions})


def rejoin_view(request):
    """Allow a force-logged-out participant to rejoin using their username + rejoin_code."""
    if request.method == 'POST':
        username    = request.POST.get('username', '').strip()
        rejoin_code = request.POST.get('rejoin_code', '').strip().upper()

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            messages.error(request, 'Username not found.')
            return render(request, 'accounts/login.html', {
                'form': LoginForm(), 'show_rejoin': True
            })

        from apps.contests.models import ContestParticipant
        cp = ContestParticipant.objects.filter(
            user=user, rejoin_code=rejoin_code
        ).select_related('contest').first()

        if not cp:
            messages.error(request, 'Invalid rejoin code. Please ask the admin for your code.')
            return render(request, 'accounts/login.html', {
                'form': LoginForm(), 'show_rejoin': True
            })

        # Clear the kick lock and rejoin code so they can re-enter
        cp.kicked_at = None
        cp.rejoin_code = ''
        cp.save(update_fields=['kicked_at', 'rejoin_code'])

        # Log the user back in
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(
            request,
            f'Welcome back, {user.username}! You have been rejoined to {cp.contest.title}.'
        )
        # Redirect back to the contest section view
        from apps.contests.models import Section
        first_section = Section.objects.filter(contest=cp.contest).order_by('order').first()
        if first_section:
            return redirect('section_view', contest_id=cp.contest.id, section_id=first_section.id)
        return redirect('contest_detail', contest_id=cp.contest.id)

    # GET — show login page with rejoin tab open
    return render(request, 'accounts/login.html', {
        'form': LoginForm(), 'show_rejoin': True
    })