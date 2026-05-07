import base64
import json
import os
import random
import string

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from apps.accounts.models import User
from .models import (
    Contest, Section, Question, TestCase,
    ContestParticipant, UserSectionProgress, SecurityEvent
)
from .forms import ContestForm, SectionForm, QuestionForm, TestCaseFormSet
from functools import wraps


# ── Helpers ──────────────────────────────────────────────────
def _get_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')


def _gen_rejoin_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


# ── Decorator ────────────────────────────────────────────────
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_contest_admin:
            messages.error(request, 'Admin access required.')
            return redirect('contest_list')
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Candidate Views ───────────────────────────────────────────
@login_required
def contest_list(request):
    if request.user.is_authenticated and request.user.is_contest_admin:
        contests = Contest.objects.all().annotate(
            participant_count=Count('participants')
        )
    else:
        contests = Contest.objects.exclude(status='draft').annotate(
            participant_count=Count('participants')
        )
    return render(request, 'contests/list.html', {'contests': contests})


@login_required
def contest_detail(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    is_enrolled = ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).exists()
    sections = contest.sections.prefetch_related('questions').all()
    section_progress = {}
    if is_enrolled:
        # Sort sections by order to check dependencies
        sorted_sections = sorted(sections, key=lambda x: x.order)
        for i, s in enumerate(sorted_sections):
            prog, _ = UserSectionProgress.objects.get_or_create(
                user=request.user, section=s
            )
            prog.lock_if_expired()
            
            # Accessibility logic
            s.can_access = True
            if s.order >= 3:
                # Check if ALL previous sections are locked
                prev_locked = True
                for prev_s in sorted_sections[:i]:
                    prev_p = section_progress.get(prev_s.id)
                    if prev_p and not prev_p.is_locked:
                        prev_locked = False
                        break
                s.can_access = prev_locked
                
            section_progress[s.id] = prog

        first_available_section = None
        first_available_progress = None
        for s in sorted_sections:
            prog = section_progress[s.id]
            if s.can_access and not prog.is_locked:
                first_available_section = s
                first_available_progress = prog
                break

    return render(request, 'contests/detail.html', {
        'contest': contest,
        'is_enrolled': is_enrolled,
        'sections': sections,
        'section_progress': section_progress,
        'first_available_section': first_available_section if is_enrolled else None,
        'first_available_progress': first_available_progress if is_enrolled else None,
    })


@login_required
def contest_register(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    if contest.has_ended:
        messages.error(request, 'Contest has already ended.')
        return redirect('contest_detail', contest_id=contest_id)
    ContestParticipant.objects.get_or_create(contest=contest, user=request.user)
    messages.success(request, f'You are now registered for {contest.title}!')
    return redirect('contest_detail', contest_id=contest_id)


@login_required
def section_view(request, contest_id, section_id):
    contest = get_object_or_404(Contest, id=contest_id)
    section = get_object_or_404(Section, id=section_id, contest=contest)

    if not ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).exists():
        messages.error(request, 'You are not registered for this contest.')
        return redirect('contest_detail', contest_id=contest_id)

    if not contest.is_active and not request.user.is_contest_admin:
        messages.error(request, 'Contest is not currently active.')
        return redirect('contest_detail', contest_id=contest_id)

    # ── Section C gate: Section B timer must be expired ──────
    if section.order >= 3:
        prev_sections = Section.objects.filter(
            contest=contest, order__lt=section.order
        ).order_by('-order')
        for prev in prev_sections:
            prev_prog, _ = UserSectionProgress.objects.get_or_create(
                user=request.user, section=prev
            )
            prev_prog.lock_if_expired()
            if not prev_prog.is_locked:
                messages.error(
                    request,
                    f'⏳ Section {prev.name} timer must finish before you can access this section.'
                )
                return redirect('section_view', contest_id=contest_id, section_id=prev.id)

    progress, _ = UserSectionProgress.objects.get_or_create(
        user=request.user, section=section
    )
    if progress.started_at is None:
        progress.started_at = timezone.now()
        progress.save()

    progress.lock_if_expired()

    questions = section.questions.prefetch_related('test_cases').all()

    from apps.submissions.models import Submission
    submissions_map = {}
    for q in questions:
        last = Submission.objects.filter(
            user=request.user, question=q
        ).order_by('-submitted_at').first()
        submissions_map[q.id] = last

    # Build all sections with their progress for the navbar
    all_sections = contest.sections.all()
    all_section_progress = {}
    for s in all_sections:
        sp, _ = UserSectionProgress.objects.get_or_create(user=request.user, section=s)
        sp.lock_if_expired()
        all_section_progress[s.id] = sp

    return render(request, 'contests/section.html', {
        'contest': contest,
        'section': section,
        'progress': progress,
        'questions': questions,
        'submissions_map': submissions_map,
        'seconds_remaining': progress.seconds_remaining(),
        'all_sections': all_sections,
        'all_section_progress': all_section_progress,
    })


@login_required
def section_timer_api(request, section_id):
    section = get_object_or_404(Section, id=section_id)
    progress, _ = UserSectionProgress.objects.get_or_create(
        user=request.user, section=section
    )
    progress.lock_if_expired()
    return JsonResponse({
        'seconds_remaining': progress.seconds_remaining(),
        'is_locked': progress.is_locked,
    })


# ── Security Event (tab-switch detection) ────────────────────
@login_required
@require_POST
def security_event(request):
    """
    Called by JS when participant switches tab / loses window focus.
    Saves screenshot (base64→PNG), logs the event, force-logs out the user,
    and sets a rejoin_code on the ContestParticipant.
    """
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    contest_id   = body.get('contest_id')
    screenshot_b64 = body.get('screenshot', '')

    contest = get_object_or_404(Contest, id=contest_id)
    cp = ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).first()
    if not cp:
        return JsonResponse({'error': 'Not enrolled'}, status=403)

    # Generate rejoin code and save it
    rejoin_code = _gen_rejoin_code()
    cp.rejoin_code = rejoin_code
    cp.save(update_fields=['rejoin_code'])

    # Save security event with screenshot
    event = SecurityEvent(
        user=request.user,
        contest=contest,
        participant=cp,
        event_type=SecurityEvent.EVENT_TAB_SWITCH,
        ip_address=_get_ip(request),
    )

    if screenshot_b64:
        try:
            # Strip data URI prefix if present
            if ',' in screenshot_b64:
                screenshot_b64 = screenshot_b64.split(',', 1)[1]
            img_data = base64.b64decode(screenshot_b64)
            from django.core.files.base import ContentFile
            fname = f"evt_{request.user.id}_{int(timezone.now().timestamp())}.png"
            event.screenshot.save(fname, ContentFile(img_data), save=False)
        except Exception:
            pass  # screenshot optional, don't crash

    event.save()

    # Force logout
    logout(request)

    return JsonResponse({
        'kicked': True,
        'rejoin_code': rejoin_code,
        'message': 'You have been logged out for switching tabs.',
    })


# ── Admin Views ───────────────────────────────────────────────
@admin_required
def admin_contest_create(request):
    if request.method == 'POST':
        form = ContestForm(request.POST)
        if form.is_valid():
            contest = form.save(commit=False)
            contest.created_by = request.user
            contest.save()
            messages.success(request, 'Contest created successfully!')
            return redirect('admin_contest_detail', contest_id=contest.id)
    else:
        form = ContestForm()
    return render(request, 'contests/admin/contest_form.html', {
        'form': form, 'action': 'Create'
    })


@login_required
@admin_required
def admin_contest_detail(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    sections = contest.sections.prefetch_related('questions').order_by('order')
    participants = ContestParticipant.objects.filter(
        contest=contest
    ).select_related('user').order_by('registered_at')

    return render(request, 'contests/admin/contest_detail.html', {
        'contest': contest,
        'sections': sections,
        'participants': participants,
    })


@admin_required
def admin_contest_edit(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    if request.method == 'POST':
        form = ContestForm(request.POST, instance=contest)
        if form.is_valid():
            form.save()
            messages.success(request, 'Contest updated!')
            return redirect('admin_contest_detail', contest_id=contest.id)
    else:
        form = ContestForm(instance=contest)
    return render(request, 'contests/admin/contest_form.html', {
        'form': form, 'contest': contest, 'action': 'Edit'
    })


@admin_required
def admin_contest_status(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Contest.STATUS_CHOICES):
            contest.status = new_status
            contest.save()
            messages.success(request, f'Status changed to {new_status}.')
    return redirect('admin_contest_detail', contest_id=contest_id)


@admin_required
def admin_section_create(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    if request.method == 'POST':
        form = SectionForm(request.POST)
        if form.is_valid():
            section = form.save(commit=False)
            section.contest = contest
            section.save()
            messages.success(request, 'Section created!')
            return redirect('admin_contest_detail', contest_id=contest.id)
    else:
        form = SectionForm()
    return render(request, 'contests/admin/section_form.html', {
        'form': form, 'contest': contest, 'action': 'Create'
    })


@admin_required
def admin_section_edit(request, section_id):
    section = get_object_or_404(Section, id=section_id)
    if request.method == 'POST':
        form = SectionForm(request.POST, instance=section)
        if form.is_valid():
            form.save()
            messages.success(request, 'Section updated!')
            return redirect('admin_contest_detail', contest_id=section.contest.id)
    else:
        form = SectionForm(instance=section)
    return render(request, 'contests/admin/section_form.html', {
        'form': form, 'contest': section.contest, 'action': 'Edit'
    })


@admin_required
def admin_question_create(request, section_id):
    section = get_object_or_404(Section, id=section_id)
    if request.method == 'POST':
        form = QuestionForm(request.POST)
        formset = TestCaseFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            question = form.save(commit=False)
            question.section = section
            question.save()
            formset.instance = question
            formset.save()
            messages.success(request, 'Question created!')
            return redirect('admin_contest_detail', contest_id=section.contest.id)
    else:
        form = QuestionForm()
        formset = TestCaseFormSet()
    return render(request, 'contests/admin/question_form.html', {
        'form': form, 'formset': formset,
        'section': section, 'action': 'Create'
    })


@admin_required
def admin_question_edit(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    section = question.section
    if request.method == 'POST':
        form = QuestionForm(request.POST, instance=question)
        formset = TestCaseFormSet(request.POST, instance=question)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, 'Question updated!')
            return redirect('admin_contest_detail', contest_id=section.contest.id)
    else:
        form = QuestionForm(instance=question)
        formset = TestCaseFormSet(instance=question)
    return render(request, 'contests/admin/question_form.html', {
        'form': form, 'formset': formset,
        'section': section, 'question': question, 'action': 'Edit'
    })


@admin_required
def admin_add_participant(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        try:
            user = User.objects.get(username=username)
            _, created = ContestParticipant.objects.get_or_create(
                contest=contest, user=user
            )
            if created:
                messages.success(request, f'{username} added to contest.')
            else:
                messages.info(request, f'{username} is already enrolled.')
        except User.DoesNotExist:
            messages.error(request, f'User "{username}" not found.')
    return redirect('admin_contest_detail', contest_id=contest_id)


@admin_required
def admin_submissions_view(request, contest_id):
    from apps.submissions.models import Submission
    contest = get_object_or_404(Contest, id=contest_id)
    submissions = Submission.objects.filter(
        question__section__contest=contest
    ).select_related('user', 'question').order_by('-submitted_at')
    return render(request, 'contests/admin/submissions.html', {
        'contest': contest,
        'submissions': submissions,
    })


# ── Participant Kick (single-session lock) ────────────────────
@csrf_exempt
@require_POST
def kick_participant(request):
    """
    Called when participant clicks 'Exit' on the fullscreen warning, or when
    the browser tab is closed (via navigator.sendBeacon).
    Sets kicked_at and generates a rejoin_code. Blocks normal login until admin
    provides the code.
    """
    if not request.user.is_authenticated:
        # sendBeacon fires after session may have ended; try to identify via body
        return JsonResponse({'status': 'ignored'}, status=200)

    try:
        body = json.loads(request.body)
        contest_id = body.get('contest_id')
    except Exception:
        contest_id = request.POST.get('contest_id')

    cp = ContestParticipant.objects.filter(
        user=request.user,
        contest_id=contest_id
    ).first()

    if cp and not cp.kicked_at:
        cp.kicked_at = timezone.now()
        # Generate a fresh rejoin code
        cp.rejoin_code = ''.join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        cp.save(update_fields=['kicked_at', 'rejoin_code'])

    return JsonResponse({'status': 'kicked', 'rejoin_code': cp.rejoin_code if cp else ''})


# ── Security Event (screenshot on tab-switch / fullscreen exit) ──
@csrf_exempt
@require_POST
def security_event(request):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'ignored'}, status=200)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    contest_id = body.get('contest_id')
    screenshot_data = body.get('screenshot', '')
    message = body.get('message', 'Security violation')

    contest = Contest.objects.filter(id=contest_id).first()
    if not contest:
        return JsonResponse({'error': 'Contest not found'}, status=404)

    cp = ContestParticipant.objects.filter(
        user=request.user, contest=contest
    ).first()

    # Save screenshot if provided (base64 → file)
    screenshot_file = None
    if screenshot_data and screenshot_data.startswith('data:image'):
        import base64, uuid
        from django.core.files.base import ContentFile
        header, encoded = screenshot_data.split(',', 1)
        img_bytes = base64.b64decode(encoded)
        filename = f'security_{request.user.id}_{uuid.uuid4().hex[:8]}.png'
        screenshot_file = ContentFile(img_bytes, name=filename)

    event = SecurityEvent(
        user=request.user,
        contest=contest,
        participant=cp,
        event_type=SecurityEvent.EVENT_TAB_SWITCH,
        ip_address=_get_ip(request),
        details=message,
    )
    if screenshot_file:
        event.screenshot.save(screenshot_file.name, screenshot_file, save=False)
    event.save()

    return JsonResponse({'status': 'logged'})