import logging
from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=5)
def evaluate_submission(self, submission_id: int):
    from .models import Submission, SubmissionTestResult
    from .executor import run_code_batch
    from apps.contests.models import TestCase

    try:
        submission = Submission.objects.select_related(
            'question', 'user'
        ).get(id=submission_id)
    except Submission.DoesNotExist:
        logger.error(f'Submission {submission_id} not found')
        return

    submission.status = Submission.STATUS_RUNNING
    submission.save(update_fields=['status'])

    question   = submission.question
    test_cases = list(TestCase.objects.filter(
        question=question
    ).order_by('order'))

    if not test_cases:
        submission.status = Submission.STATUS_WRONG
        submission.score  = 0
        submission.save(update_fields=['status', 'score'])
        logger.warning(f'Submission {submission_id}: no test cases found')
        return

    n = len(test_cases)

    # ── Prepare all inputs (normalize line endings) ───────────
    clean_inputs = [
        (tc.input_data or '').replace('\r\n', '\n').replace('\r', '\n')
        for tc in test_cases
    ]

    # ── Run ALL test cases in a SINGLE process/container ──────
    batch_results = run_code_batch(
        code=submission.code,
        inputs=clean_inputs,
        time_limit_sec=question.time_limit,
        language=submission.language,
    )

    # ── Process results ───────────────────────────────────────
    results       = []
    total_time_ms = 0.0
    passed        = 0

    for tc, result in zip(test_cases, batch_results):
        expected = (tc.expected_output or '').replace('\r\n', '\n').replace('\r', '\n').strip()
        actual   = result.stdout.replace('\r\n', '\n').replace('\r', '\n').strip()

        # ── Log each test case result for debugging ───────────
        logger.debug(
            f'TC-{tc.order} | exit={result.exit_code} '
            f'tle={result.tle} re={result.runtime_error} '
            f'time={result.execution_time_ms:.0f}ms | '
            f'expected={repr(expected[:50])} got={repr(actual[:50])}'
        )

        if result.tle:
            status = SubmissionTestResult.STATUS_TLE
        elif result.mle:
            status = SubmissionTestResult.STATUS_MLE
        elif result.runtime_error:
            status = SubmissionTestResult.STATUS_RE
        elif actual == expected:
            status = SubmissionTestResult.STATUS_PASS
            passed += 1
        else:
            status = SubmissionTestResult.STATUS_FAIL

        total_time_ms += result.execution_time_ms

        results.append(SubmissionTestResult(
            submission=submission,
            test_case=tc,
            status=status,
            actual_output=actual,
            execution_time=result.execution_time_ms,
        ))

    with transaction.atomic():
        SubmissionTestResult.objects.filter(submission=submission).delete()
        SubmissionTestResult.objects.bulk_create(results)

        score = round((passed / n) * question.marks, 2) if n > 0 else 0

        if passed == n:
            submission.status = Submission.STATUS_ACCEPTED
        elif passed > 0:
            submission.status = Submission.STATUS_PARTIAL
        else:
            # Map first failure type to submission status
            first_fail = next(
                (r for r in results if r.status != SubmissionTestResult.STATUS_PASS),
                None
            )
            if first_fail:
                mapping = {
                    SubmissionTestResult.STATUS_TLE:  Submission.STATUS_TLE,
                    SubmissionTestResult.STATUS_RE:   Submission.STATUS_RE,
                    SubmissionTestResult.STATUS_MLE:  Submission.STATUS_MLE,
                    SubmissionTestResult.STATUS_FAIL: Submission.STATUS_WRONG,
                }
                submission.status = mapping.get(first_fail.status, Submission.STATUS_WRONG)
            else:
                submission.status = Submission.STATUS_WRONG

        submission.score          = score
        submission.execution_time = total_time_ms / n if n > 0 else 0
        submission.save(update_fields=['status', 'score', 'execution_time'])

    # ── WebSocket push ────────────────────────────────────────
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'submission_{submission_id}',
            {
                'type': 'submission_result',
                'submission_id': submission_id,
                'status': submission.status,
                'score': float(submission.score),
            }
        )
        contest_id = question.section.contest_id
        async_to_sync(channel_layer.group_send)(
            f'leaderboard_{contest_id}',
            {'type': 'leaderboard_update', 'contest_id': contest_id}
        )
    except Exception as e:
        logger.warning(f'WebSocket notify failed: {e}')

    logger.info(
        f'Submission {submission_id}: {submission.status} '
        f'({passed}/{n}) score={submission.score}'
    )
    return {'status': submission.status, 'score': submission.score}