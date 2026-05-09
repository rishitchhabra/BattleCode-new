from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Max
from apps.contests.models import Contest, Section, Question, ContestParticipant
from apps.submissions.models import Submission


def get_leaderboard_data(contest):
    sections  = list(Section.objects.filter(contest=contest).prefetch_related('questions').order_by('order'))
    questions = list(Question.objects.filter(section__contest=contest).select_related('section').order_by('section__order', 'order'))

    participants = list(ContestParticipant.objects.filter(
        contest=contest
    ).select_related('user').order_by('registered_at'))

    if not participants:
        return [], sections, questions

    user_ids = [cp.user_id for cp in participants]

    # Best score per user per question
    best_scores = Submission.objects.filter(
        question__section__contest=contest,
        user_id__in=user_ids,
    ).values('user_id', 'question_id').annotate(best_score=Max('score'))

    # score_map[user_id][question_id] = best_score
    score_map = {}
    for row in best_scores:
        uid = row['user_id']
        qid = row['question_id']
        if uid not in score_map:
            score_map[uid] = {}
        score_map[uid][qid] = row['best_score']

    # Best submission per (user, question) for code display — any status
    best_subs = {}
    all_subs = Submission.objects.filter(
        question__section__contest=contest,
        user_id__in=user_ids,
    ).order_by('user_id', 'question_id', '-score', 'submitted_at')

    for sub in all_subs:
        key = (sub.user_id, sub.question_id)
        if key not in best_subs:
            best_subs[key] = sub

    # Build attempt count map from ContestParticipant
    attempt_map = {}
    for cp in participants:
        attempt_map[cp.user_id] = {
            'compile_count': cp.compile_count,
            'submit_count': cp.submit_count,
            'total_attempts': cp.total_attempts,
        }

    # Build rows
    rows = []
    for cp in participants:
        uid         = cp.user_id
        user_scores = score_map.get(uid, {})
        total_score = round(sum(user_scores.values()), 2)
        q_solved    = sum(1 for v in user_scores.values() if v > 0)
        attempts    = attempt_map.get(uid, {})

        question_data = []
        for q in questions:
            score = user_scores.get(q.id)
            sub   = best_subs.get((uid, q.id))
            question_data.append({
                'question':   q,
                'score':      score,
                'code':       sub.code if sub else None,
                'sub_id':     sub.id   if sub else None,
                'sub_status': sub.status if sub else None,
            })

        rows.append({
            'user':             cp.user,
            'total_score':      total_score,
            'questions_solved': q_solved,
            'question_data':    question_data,
            'compile_count':    attempts.get('compile_count', 0),
            'submit_count':     attempts.get('submit_count', 0),
            'total_attempts':   attempts.get('total_attempts', 0),
        })

    # Sort: highest score first, then fewest total attempts as tie-breaker
    rows.sort(key=lambda r: (-r['total_score'], r['total_attempts']))
    for i, row in enumerate(rows):
        row['rank'] = i + 1

    return rows, sections, questions


def _compute_rankings(contest_id):
    """Legacy API format for WebSocket consumer compatibility."""
    participants = ContestParticipant.objects.filter(
        contest_id=contest_id
    ).select_related('user')

    rows = []
    for cp in participants:
        user = cp.user
        best_scores = (
            Submission.objects
            .filter(user=user, question__section__contest_id=contest_id)
            .values('question_id')
            .annotate(best=Max('score'))
        )
        total            = sum(r['best'] for r in best_scores)
        questions_solved = sum(1 for r in best_scores if r['best'] > 0)

        last_accepted = (
            Submission.objects
            .filter(
                user=user,
                question__section__contest_id=contest_id,
                status='accepted',
            )
            .order_by('-submitted_at')          # ✅ submitted_at
            .values_list('submitted_at', flat=True)
            .first()
        )

        # Get attempt counts
        cp_obj = cp
        compile_count = cp_obj.compile_count
        submit_count = cp_obj.submit_count
        total_attempts_val = compile_count + submit_count

        rows.append({
            'user_id':          user.id,
            'username':         user.username,
            'total_score':      round(total, 2),
            'questions_solved': questions_solved,
            'last_accepted':    last_accepted.isoformat() if last_accepted else None,
            'compile_count':    compile_count,
            'submit_count':     submit_count,
            'total_attempts':   total_attempts_val,
        })

    rows.sort(key=lambda r: (-r['total_score'], r['total_attempts']))
    for i, row in enumerate(rows, 1):
        row['rank'] = i
    return rows


@login_required
def leaderboard_view(request, contest_id):
    contest = get_object_or_404(Contest, id=contest_id)
    rows, sections, questions = get_leaderboard_data(contest)
    return render(request, 'leaderboard/leaderboard.html', {
        'contest':   contest,
        'rows':      rows,
        'sections':  sections,
        'questions': questions,
    })


@login_required
def leaderboard_api(request, contest_id):
    rankings = _compute_rankings(contest_id)
    return JsonResponse({'rankings': rankings})