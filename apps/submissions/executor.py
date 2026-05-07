import base64
import subprocess
import sys
import time
import tempfile
import os
from django.conf import settings

# ── Config ────────────────────────────────────────────────────
TIMEOUT       = getattr(settings, 'EXECUTOR_TIMEOUT', 30)
MEMORY_LIMIT  = getattr(settings, 'EXECUTOR_MEMORY_LIMIT', '128m')
CPU_QUOTA     = getattr(settings, 'EXECUTOR_CPU_QUOTA', 50000)
IMAGE         = getattr(settings, 'EXECUTOR_IMAGE', 'python:3.11-alpine')

# Set USE_DOCKER_EXECUTOR=True in settings/env to force Docker.
# Defaults to False for local dev (subprocess is ~50x faster).
USE_DOCKER    = getattr(settings, 'USE_DOCKER_EXECUTOR', False)

# ── Security wrapper (used by BOTH backends) ──────────────────
WRAPPER = r'''
import sys, os as _os, builtins as _b, base64, io as _io

# ── Decode code and stdin from env vars ───────────────────────
_code_b64  = _os.environ.get('USER_CODE', '')
_stdin_b64 = _os.environ.get('USER_STDIN', '')
_code      = base64.b64decode(_code_b64).decode('utf-8')
_stdin_str = base64.b64decode(_stdin_b64).decode('utf-8')

# ── Block filesystem writes outside /tmp ──────────────────────
_real_open = _b.open
def _safe_open(f, mode='r', *a, **kw):
    if any(m in mode for m in ('w', 'a', 'x', '+')):
        if not str(f).startswith(('/tmp', 'temp', 'Temp')):
            raise PermissionError('Write outside /tmp is blocked')
    return _real_open(f, mode, *a, **kw)
_b.open = _safe_open

# ── Block shell access ────────────────────────────────────────
def _blocked(*a, **k): raise PermissionError('System calls are blocked')
_os.system = _blocked
_os.popen  = _blocked

# ── Redirect stdin ────────────────────────────────────────────
sys.stdin = _io.StringIO(_stdin_str)

# ── Execute user code ─────────────────────────────────────────
exec(compile(_code, '<solution>', 'exec'), {'__name__': '__main__'})
'''

# ── Docker-only TLE wrapper (SIGALRM only works on Linux) ─────
WRAPPER_DOCKER = r'''
import sys, signal, resource, os as _os, builtins as _b, base64

_code_b64  = _os.environ.get('USER_CODE', '')
_stdin_b64 = _os.environ.get('USER_STDIN', '')
_code      = base64.b64decode(_code_b64).decode('utf-8')
_stdin_str = base64.b64decode(_stdin_b64).decode('utf-8')

def _tle(s, f):
    sys.stderr.write('TLE\n')
    sys.exit(124)
signal.signal(signal.SIGALRM, _tle)
signal.alarm({time_limit})

_mem = {mem}
resource.setrlimit(resource.RLIMIT_AS, (_mem, _mem))

_real_open = _b.open
def _safe_open(f, mode='r', *a, **kw):
    if any(m in mode for m in ('w', 'a', 'x', '+')):
        if not str(f).startswith('/tmp'):
            raise PermissionError('Write outside /tmp is blocked')
    return _real_open(f, mode, *a, **kw)
_b.open = _safe_open

def _blocked(*a, **k): raise PermissionError('System calls are blocked')
_os.system = _blocked
_os.popen  = _blocked

import io as _io
sys.stdin = _io.StringIO(_stdin_str)

exec(compile(_code, '<solution>', 'exec'), {{'__name__': '__main__'}})
'''


class ExecutionResult:
    def __init__(self, stdout='', stderr='', exit_code=0,
                 execution_time_ms=0.0, tle=False, mle=False):
        self.stdout            = stdout.strip()
        self.stderr            = stderr.strip()
        self.exit_code         = exit_code
        self.execution_time_ms = execution_time_ms
        self.tle               = tle or (exit_code == 124)
        self.mle               = mle
        self.runtime_error     = (
            exit_code not in (0, 124) and not mle
        )

    @property
    def passed(self):
        return self.exit_code == 0 and not self.tle and not self.mle

    def __repr__(self):
        return (
            f'<ExecutionResult exit={self.exit_code} '
            f'tle={self.tle} mle={self.mle} '
            f're={self.runtime_error} '
            f'time={self.execution_time_ms:.1f}ms>'
        )


# ═══════════════════════════════════════════════════════════════
# FAST BACKEND — subprocess (local dev / non-Docker)
# ~100ms per test case vs 3–8s for Docker cold start
# ═══════════════════════════════════════════════════════════════

def _run_subprocess(code: str, stdin_data: str,
                    time_limit_sec: float = 2.0) -> ExecutionResult:
    """Run user code in a subprocess with timeout. Fast for local dev."""
    code_b64  = base64.b64encode(code.encode('utf-8')).decode('ascii')
    stdin_b64 = base64.b64encode((stdin_data or '').encode('utf-8')).decode('ascii')

    env = os.environ.copy()
    env['USER_CODE']  = code_b64
    env['USER_STDIN'] = stdin_b64
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, '-u', '-c', WRAPPER],
            env=env,
            capture_output=True,
            timeout=time_limit_sec + 2,   # wall-clock limit
            text=True,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        tle = elapsed_ms > (time_limit_sec * 1000 + 500)

        mle = (
            'MemoryError' in proc.stdout
            or 'MemoryError' in proc.stderr
        )

        return ExecutionResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time_ms=elapsed_ms,
            tle=tle,
            mle=mle,
        )

    except subprocess.TimeoutExpired:
        elapsed_ms = (time.monotonic() - start) * 1000
        return ExecutionResult(
            stderr='Time Limit Exceeded',
            exit_code=124,
            execution_time_ms=elapsed_ms,
            tle=True,
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        return ExecutionResult(
            stderr=f'Execution error: {type(e).__name__}: {e}',
            exit_code=1,
            execution_time_ms=elapsed_ms,
        )


# ═══════════════════════════════════════════════════════════════
# SECURE BACKEND — Docker (production)
# Full isolation, SIGALRM TLE, memory limits, read-only FS
# ═══════════════════════════════════════════════════════════════

def _run_docker(code: str, stdin_data: str,
                time_limit_sec: float = 2.0) -> ExecutionResult:
    """Run user code in a sandboxed Docker container."""
    try:
        import docker as docker_lib
        client = docker_lib.from_env(timeout=30)
    except Exception as e:
        return ExecutionResult(stderr=f'Docker unavailable: {e}', exit_code=1)

    code_b64  = base64.b64encode(code.encode('utf-8')).decode('ascii')
    stdin_b64 = base64.b64encode((stdin_data or '').encode('utf-8')).decode('ascii')

    mem_bytes  = 128 * 1024 * 1024
    bootstrap  = WRAPPER_DOCKER.format(
        time_limit=max(int(time_limit_sec) + 2, 5),
        mem=mem_bytes,
    )

    container = None
    try:
        container = client.containers.run(
            image=IMAGE,
            command=['python', '-u', '-c', bootstrap],
            network_disabled=True,
            mem_limit=MEMORY_LIMIT,
            memswap_limit=MEMORY_LIMIT,
            cpu_quota=CPU_QUOTA,
            pids_limit=64,
            read_only=True,
            tmpfs={'/tmp': 'size=16m,noexec'},
            security_opt=['no-new-privileges'],
            cap_drop=['ALL'],
            detach=True,
            remove=False,
            environment={
                'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONUNBUFFERED': '1',
                'USER_CODE':  code_b64,
                'USER_STDIN': stdin_b64,
            },
        )

        # Wait for container to start (max 20s)
        startup_deadline = time.monotonic() + 20
        while time.monotonic() < startup_deadline:
            container.reload()
            if container.status in ('running', 'exited', 'dead'):
                break
            time.sleep(0.05)

        exec_start = time.monotonic()
        wall_limit = time_limit_sec + 5
        timed_out  = False
        try:
            exit_result = container.wait(timeout=wall_limit)
            exit_code   = exit_result.get('StatusCode', -1)
        except Exception:
            timed_out = True
            exit_code = 124
            try:
                container.kill()
            except Exception:
                pass

        elapsed_ms = (time.monotonic() - exec_start) * 1000

        try:
            stdout_raw = container.logs(stdout=True,  stderr=False)
            stderr_raw = container.logs(stdout=False, stderr=True)
        except Exception:
            stdout_raw = b''
            stderr_raw = b''

        stdout_str = stdout_raw.decode('utf-8', errors='replace')
        stderr_str = stderr_raw.decode('utf-8', errors='replace')

        tle = (
            timed_out
            or exit_code == 124
            or elapsed_ms > (time_limit_sec * 1000 + 500)
            or 'TLE' in stderr_str
        )
        mle = (
            'MemoryError' in stdout_str
            or 'MemoryError' in stderr_str
            or 'Killed'     in stderr_str
        )

        return ExecutionResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code,
            execution_time_ms=elapsed_ms,
            tle=tle,
            mle=mle,
        )

    except Exception as e:
        return ExecutionResult(
            stderr=f'Execution error: {type(e).__name__}: {e}',
            exit_code=1,
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# PUBLIC API — auto-selects backend
# ═══════════════════════════════════════════════════════════════

def run_code(code: str, stdin_data: str,
             time_limit_sec: float = 2.0) -> ExecutionResult:
    """
    Run user Python code with the appropriate backend.

    - Local dev  → subprocess (~100ms/test, no Docker required)
    - Production → Docker    (full sandbox isolation)

    Controlled by settings.USE_DOCKER_EXECUTOR (default: False).
    Set USE_DOCKER_EXECUTOR=True in .env.prod or settings_prod.py.
    """
    if USE_DOCKER:
        return _run_docker(code, stdin_data, time_limit_sec)
    return _run_subprocess(code, stdin_data, time_limit_sec)