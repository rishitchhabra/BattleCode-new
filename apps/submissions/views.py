from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
import json as _json
from .models import Submission, SubmissionTestResult, QuestionDraft
from .tasks import evaluate_submission
from apps.contests.models import Question, ContestParticipant, UserSectionProgress


@login_required
@require_POST
def submit_code(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    section  = question.section
    contest  = section.contest

    if not ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).exists():
        return JsonResponse({'error': 'Not enrolled.'}, status=403)

    progress = UserSectionProgress.objects.filter(
        user=request.user, section=section
    ).first()
    if progress:
        progress.lock_if_expired()
        if progress.is_locked:
            return JsonResponse({'error': 'Section time has expired.'}, status=403)

    # ── Input parsing ─────────────────────────────
    if request.content_type and 'application/json' in request.content_type:
        try:
            body = _json.loads(request.body)
            code = body.get('code', '').strip()
        except Exception:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
    else:
        code = request.POST.get('code', '').strip()

    if len(code) > 65536:
        return JsonResponse({'error': 'Code too large (max 64 KB).'}, status=400)

    # ── Save / Update submission ─────────────────
    submission = Submission.objects.filter(
        user=request.user, question=question
    ).first()

    if submission:
        submission.code = code
        submission.status = Submission.STATUS_PENDING
        submission.save(update_fields=['code', 'status'])
        submission.test_results.all().delete()
    else:
        submission = Submission.objects.create(
            user=request.user,
            question=question,
            code=code,
            language='python',
            status=Submission.STATUS_PENDING,
        )

    # ── CELERY SAFE CALL (FIXED PART) ───────────
    try:
        task = evaluate_submission.apply_async(args=[submission.id])
        submission.task_id = task.id
        submission.save(update_fields=['task_id'])
    except Exception as e:
        import logging
        logging.error(f"Celery broker error: {e}")

        submission.status = Submission.STATUS_FAILED
        submission.save(update_fields=['status'])

        return JsonResponse({
            'error': 'Submission system temporarily unavailable. Try again.'
        }, status=500)

    return JsonResponse({
        'submission_id': submission.id,
        'status': submission.status,
        'message': 'Submission received. Evaluating…',
    })


@login_required
def submission_status_api(request, submission_id):
    submission = get_object_or_404(Submission, id=submission_id, user=request.user)
    return JsonResponse({
        'status':         submission.status,
        'score':          float(submission.score),
        'display_status': submission.get_status_display(),
        'is_final':       submission.status not in (
            Submission.STATUS_PENDING, Submission.STATUS_RUNNING
        ),
    })


@login_required
def submission_detail(request, submission_id):
    submission = get_object_or_404(Submission, id=submission_id, user=request.user)
    sample_results = []
    hidden_summary = []

    for tr in submission.test_results.select_related('test_case').all():
        if not tr.test_case.is_hidden:
            sample_results.append(tr)
        else:
            hidden_summary.append(tr)

    return render(request, 'submissions/result.html', {
        'submission':     submission,
        'sample_results': sample_results,
        'hidden_summary': hidden_summary,
    })


@login_required
def question_editor(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    section  = question.section
    contest  = section.contest

    if not ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).exists():
        messages.error(request, 'You are not registered for this contest.')
        return redirect('contest_detail', contest_id=contest.id)

    # Determine section access control BEFORE starting timers
    all_sections = list(contest.sections.order_by('order'))
    all_section_progress = {}
    for i, s in enumerate(all_sections):
        sp, _ = UserSectionProgress.objects.get_or_create(user=request.user, section=s)
        sp.lock_if_expired()
        
        s.can_access = True
        if sp.is_locked:
            s.can_access = False
            
        if s.order >= 3 and s.can_access:
            prev_locked = True
            for prev_s in all_sections[:i]:
                prev_sp = all_section_progress.get(prev_s.id)
                if prev_sp and not prev_sp.is_locked:
                    prev_locked = False
                    break
            s.can_access = prev_locked
            
        # Get first question ID for direct editor link
        first_q = s.questions.order_by('order').first()
        s.first_question_id = first_q.id if first_q else None
        
        all_section_progress[s.id] = sp

    # Enforce access control for direct URL manipulation
    current_s = next((s for s in all_sections if s.id == section.id), None)
    if current_s and not current_s.can_access:
        messages.error(request, 'You do not have access to this section right now.')
        return redirect('contest_detail', contest_id=contest.id)

    # Access is granted, ensure timer is started for current section
    progress = all_section_progress[section.id]
    if progress.started_at is None:
        from django.utils import timezone
        progress.started_at = timezone.now()
        progress.save(update_fields=['started_at'])
        progress.lock_if_expired()

    # Capture IP address on first access
    from apps.contests.models import ContestParticipant as CP
    cp = CP.objects.filter(contest=contest, user=request.user).first()
    if cp and not cp.ip_address:
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        cp.ip_address = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')
        cp.save(update_fields=['ip_address'])

    last_submission = Submission.objects.filter(
        user=request.user, question=question
    ).order_by('-submitted_at').first()

    submissions = Submission.objects.filter(
        user=request.user, question=question
    ).order_by('-submitted_at')[:10]

    # Load draft if exists, fall back to last submission code
    draft = QuestionDraft.objects.filter(user=request.user, question=question).first()
    if draft:
        initial_code = draft.code
    elif last_submission:
        initial_code = last_submission.code
    else:
        initial_code = ''

    seconds_remaining = progress.seconds_remaining()

    # All questions in this section (for navbar)
    section_questions = list(section.questions.order_by('order'))

    # Find prev/next question
    current_idx  = next((i for i, q in enumerate(section_questions) if q.id == question.id), 0)
    prev_question = section_questions[current_idx - 1] if current_idx > 0 else None
    next_question = section_questions[current_idx + 1] if current_idx < len(section_questions) - 1 else None

    # Draft codes for all questions in section (for JS quick-switch)
    draft_codes = {}
    for sq in section_questions:
        d = QuestionDraft.objects.filter(user=request.user, question=sq).first()
        if d:
            draft_codes[sq.id] = d.code

    # Per-question submission status for left sidebar
    question_statuses = {}
    for sq in section_questions:
        last = Submission.objects.filter(
            user=request.user, question=sq
        ).order_by('-submitted_at').first()
        question_statuses[sq.id] = {
            'submitted': last is not None,
            'score': float(last.score) if last else 0,
            'marks': sq.marks,
            'status': last.status if last else None,
        }

    # Find next section URL and next_section_first_q_id
    next_section_url = None
    next_section_first_q_id = None
    from django.urls import reverse
    for s in all_sections:
        if s.order > section.order:
            if s.can_access:
                if s.first_question_id:
                    next_section_first_q_id = s.first_question_id
                    next_section_url = reverse('question_editor', args=[s.first_question_id])
                else:
                    next_section_url = reverse('section_view', args=[contest.id, s.id])
            break

    return render(request, 'contests/editor.html', {
        'question':           question,
        'section':            section,
        'contest':            contest,
        'progress':           progress,
        'last_submission':    last_submission,
        'submissions':        submissions,
        'seconds_remaining':  seconds_remaining,
        'initial_code':       initial_code,
        'section_questions':  section_questions,
        'current_idx':        current_idx,
        'prev_question':      prev_question,
        'next_question':      next_question,
        'draft_codes':        draft_codes,
        'all_sections':       all_sections,
        'all_section_progress': all_section_progress,
        'question_statuses':  question_statuses,
        'next_section_url':   next_section_url,
        'next_section_first_q_id': next_section_first_q_id,
    })


# ── Question Data API (for AJAX in-place question switching) ──
@login_required
def question_data_api(request, question_id):
    """Return all question data as JSON so the editor can switch questions without page reload."""
    question = get_object_or_404(Question, id=question_id)
    section  = question.section
    contest  = section.contest

    if not ContestParticipant.objects.filter(contest=contest, user=request.user).exists():
        return JsonResponse({'error': 'Not enrolled.'}, status=403)

    # Determine section access control BEFORE returning data
    all_sections = list(contest.sections.order_by('order'))
    all_section_progress = {}
    for i, s in enumerate(all_sections):
        sp, _ = UserSectionProgress.objects.get_or_create(user=request.user, section=s)
        sp.lock_if_expired()
        all_section_progress[s.id] = sp
        
        s.can_access = True
        if sp.is_locked:
            s.can_access = False

        if s.order >= 3 and s.can_access:
            prev_locked = True
            for prev_s in all_sections[:i]:
                prev_sp = all_section_progress.get(prev_s.id)
                if prev_sp and not prev_sp.is_locked:
                    prev_locked = False
                    break
            s.can_access = prev_locked

    # Enforce access control for direct URL manipulation via AJAX
    current_s = next((s for s in all_sections if s.id == section.id), None)
    if current_s and not current_s.can_access:
        return JsonResponse({'error': 'You do not have access to this section right now.', 'force_redirect': f'/contests/{contest.id}/'}, status=403)

    # Access is granted, ensure timer is started for current section
    progress = all_section_progress[section.id]
    if progress.started_at is None:
        from django.utils import timezone
        progress.started_at = timezone.now()
        progress.save(update_fields=['started_at'])
        progress.lock_if_expired()

    # Section questions for nav
    section_questions = list(section.questions.order_by('order'))
    current_idx = next((i for i, q in enumerate(section_questions) if q.id == question.id), 0)
    prev_q = section_questions[current_idx - 1] if current_idx > 0 else None
    next_q = section_questions[current_idx + 1] if current_idx < len(section_questions) - 1 else None

    # Load saved draft or last submission
    draft = QuestionDraft.objects.filter(user=request.user, question=question).first()
    last_sub = Submission.objects.filter(user=request.user, question=question).order_by('-submitted_at').first()
    if draft:
        code = draft.code
    elif last_sub:
        code = last_sub.code
    else:
        code = ''

    # Submission status
    sub_status = None
    if last_sub:
        sub_status = {'status': last_sub.status, 'score': float(last_sub.score)}

    # Question statuses for the whole section (for rendering the sidebar)
    question_statuses = {}
    section_questions_data = []
    for sq in section_questions:
        last = Submission.objects.filter(
            user=request.user, question=sq
        ).order_by('-submitted_at').first()
        is_sub = last is not None
        question_statuses[sq.id] = {
            'submitted': is_sub,
            'score': float(last.score) if last else 0,
            'marks': sq.marks,
            'status': last.status if last else None,
        }
        section_questions_data.append({
            'id': sq.id,
            'order': sq.order,
            'title': sq.title,
            'submitted': is_sub,
        })

    next_section_first_q_id = None
    for s in all_sections:
        if s.order > section.order:
            if s.can_access:
                first_q = s.questions.order_by('order').first()
                if first_q:
                    next_section_first_q_id = first_q.id
            break

    # Enforce access control for AJAX URL manipulation
    current_s = next((s for s in all_sections if s.id == section.id), None)
    if current_s and not current_s.can_access:
        return JsonResponse({'error': 'You do not have access to this section right now.'}, status=403)

    curr_sp = all_section_progress.get(section.id)
    seconds_remaining = curr_sp.seconds_remaining() if curr_sp else 0

    return JsonResponse({
        'section': {
            'id': section.id,
            'name': section.name,
            'order': section.order,
            'seconds_remaining': seconds_remaining,
        },
        'question': {
            'id':                question.id,
            'title':             question.title,
            'problem_statement': question.problem_statement,
            'input_format':      question.input_format,
            'output_format':     question.output_format,
            'constraints':       question.constraints,
            'sample_input':      question.sample_input,
            'sample_output':     question.sample_output,
            'marks':             question.marks,
        },
        'code':         code,
        'current_idx':  current_idx,
        'total':        len(section_questions),
        'prev_id':      prev_q.id if prev_q else None,
        'next_id':      next_q.id if next_q else None,
        'is_last':      (next_q is None),
        'submission_status': sub_status,
        'question_statuses': question_statuses,
        'section_questions': section_questions_data,
        'next_section_first_q_id': next_section_first_q_id,
    })


# ── Save Draft ────────────────────────────────────────────────
@login_required
@require_POST
def save_draft(request, question_id):
    """Auto-save code draft without formal submission."""
    question = get_object_or_404(Question, id=question_id)

    if request.content_type and 'application/json' in request.content_type:
        try:
            body = _json.loads(request.body)
            code = body.get('code', '')
        except Exception:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
    else:
        code = request.POST.get('code', '')

    QuestionDraft.objects.update_or_create(
        user=request.user,
        question=question,
        defaults={'code': code},
    )
    return JsonResponse({'saved': True})


# ── Compile & Run (sample test cases only) ────────────────────
@login_required
@require_POST
def compile_run(request, question_id):
    """Run code against sample (non-hidden) test cases only. Fast, synchronous."""
    question = get_object_or_404(Question, id=question_id)
    section  = question.section
    contest  = section.contest

    if not ContestParticipant.objects.filter(
        contest=contest, user=request.user
    ).exists():
        return JsonResponse({'error': 'Not enrolled.'}, status=403)

    progress = UserSectionProgress.objects.filter(
        user=request.user, section=section
    ).first()
    if progress:
        progress.lock_if_expired()
        if progress.is_locked:
            return JsonResponse({'error': 'Section time has expired.'}, status=403)

    if request.content_type and 'application/json' in request.content_type:
        try:
            body = _json.loads(request.body)
            code = body.get('code', '').strip()
        except Exception:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
    else:
        code = request.POST.get('code', '').strip()

    sample_cases = list(question.test_cases.filter(is_hidden=False).order_by('order'))
    if not sample_cases:
        return JsonResponse({'error': 'No sample test cases available for this question.'})

    from apps.submissions.executor import run_code
    results = []
    all_passed = True

    for tc in sample_cases:
        result = run_code(code, tc.input_data, question.time_limit)
        passed = (
            result.exit_code == 0
            and not result.tle
            and not result.mle
            and result.stdout.strip() == tc.expected_output.strip()
        )
        if not passed:
            all_passed = False

        status = 'pass' if passed else (
            'tle' if result.tle else
            'mle' if result.mle else
            're'  if result.runtime_error else
            'wrong'
        )
        results.append({
            'tc_order':         tc.order,
            'status':           status,
            'actual_output':    result.stdout[:2000],
            'expected_output':  tc.expected_output[:2000],
            'stderr':           result.stderr[:500] if result.stderr else '',
            'execution_time_ms': round(result.execution_time_ms, 1),
        })

    return JsonResponse({
        'all_passed': all_passed,
        'results':    results,
        'total':      len(results),
        'passed':     sum(1 for r in results if r['status'] == 'pass'),
    })

@login_required
@require_POST
def lock_section(request, section_id):
    """Explicitly lock a section when a participant submits it early."""
    from apps.contests.models import Section
    section = get_object_or_404(Section, id=section_id)
    progress = UserSectionProgress.objects.filter(
        user=request.user, section=section
    ).first()
    
    if progress and not progress.is_locked:
        from django.utils import timezone
        progress.is_locked = True
        progress.ended_at = timezone.now()
        progress.save(update_fields=['is_locked', 'ended_at'])
        
    return JsonResponse({'success': True})
