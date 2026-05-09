import base64
import subprocess
import sys
import time
import tempfile
import os
import json as _json
from django.conf import settings

# ── Config ────────────────────────────────────────────────────
TIMEOUT       = getattr(settings, 'EXECUTOR_TIMEOUT', 30)
MEMORY_LIMIT  = getattr(settings, 'EXECUTOR_MEMORY_LIMIT', '128m')
CPU_QUOTA     = getattr(settings, 'EXECUTOR_CPU_QUOTA', 50000)
IMAGE         = getattr(settings, 'EXECUTOR_IMAGE', 'python:3.11-alpine')
JAVA_IMAGE    = getattr(settings, 'EXECUTOR_JAVA_IMAGE', 'openjdk:17-alpine')

# Set USE_DOCKER_EXECUTOR=True in settings/env to force Docker.
# Defaults to False for local dev (subprocess is ~50x faster).
USE_DOCKER    = getattr(settings, 'USE_DOCKER_EXECUTOR', False)

# ── Security wrapper (single test case — used by compile & run) ──
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

# ── Batch wrapper: runs ALL test cases in ONE process ─────────
# Receives code + list of inputs via env, outputs JSON results to stdout
WRAPPER_BATCH = r'''
import sys, os as _os, builtins as _b, base64, io as _io, json, time

_code_b64    = _os.environ.get('USER_CODE', '')
_inputs_json = _os.environ.get('USER_INPUTS', '[]')
_code        = base64.b64decode(_code_b64).decode('utf-8')
_inputs      = json.loads(_inputs_json)

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

# ── Compile code once ────────────────────────────────────────
try:
    _compiled = compile(_code, '<solution>', 'exec')
except SyntaxError as e:
    _results = [{'stdout': '', 'stderr': str(e), 'exit_code': 1, 'time_ms': 0.0} for _ in _inputs]
    sys.stdout.write(json.dumps(_results))
    sys.exit(0)

_results = []
for _inp in _inputs:
    _old_stdout = sys.stdout
    _old_stderr = sys.stderr
    _cap_out = _io.StringIO()
    _cap_err = _io.StringIO()
    _exit = 0
    _start = time.monotonic()
    try:
        sys.stdin  = _io.StringIO(_inp)
        sys.stdout = _cap_out
        sys.stderr = _cap_err
        exec(_compiled, {'__name__': '__main__'})
    except SystemExit:
        _exit = 1
    except Exception as e:
        _cap_err.write(str(e))
        _exit = 1
    finally:
        sys.stdout = _old_stdout
        sys.stderr = _old_stderr
    _elapsed = (time.monotonic() - _start) * 1000
    _results.append({
        'stdout':    _cap_out.getvalue(),
        'stderr':    _cap_err.getvalue(),
        'exit_code': _exit,
        'time_ms':   round(_elapsed, 2),
    })

sys.stdout.write(json.dumps(_results))
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

# ── Docker batch wrapper ──────────────────────────────────────
WRAPPER_DOCKER_BATCH = r'''
import sys, signal, resource, os as _os, builtins as _b, base64, json, io as _io, time

_code_b64    = _os.environ.get('USER_CODE', '')
_inputs_json = _os.environ.get('USER_INPUTS', '[]')
_code        = base64.b64decode(_code_b64).decode('utf-8')
_inputs      = json.loads(_inputs_json)
_time_limit  = {time_limit}

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

try:
    _compiled = compile(_code, '<solution>', 'exec')
except SyntaxError as e:
    _results = [{{'stdout': '', 'stderr': str(e), 'exit_code': 1, 'time_ms': 0.0, 'tle': False}} for _ in _inputs]
    sys.stdout.write(json.dumps(_results))
    sys.exit(0)

_results = []
for _inp in _inputs:
    _old_stdout = sys.stdout
    _old_stderr = sys.stderr
    _cap_out = _io.StringIO()
    _cap_err = _io.StringIO()
    _exit = 0
    _tle_flag = False

    def _tle_handler(s, f):
        raise TimeoutError('TLE')
    signal.signal(signal.SIGALRM, _tle_handler)
    signal.alarm(_time_limit)

    _start = time.monotonic()
    try:
        sys.stdin  = _io.StringIO(_inp)
        sys.stdout = _cap_out
        sys.stderr = _cap_err
        exec(_compiled, {{'__name__': '__main__'}})
    except TimeoutError:
        _tle_flag = True
        _exit = 124
    except SystemExit:
        _exit = 1
    except Exception as e:
        _cap_err.write(str(e))
        _exit = 1
    finally:
        signal.alarm(0)
        sys.stdout = _old_stdout
        sys.stderr = _old_stderr
    _elapsed = (time.monotonic() - _start) * 1000
    _results.append({{
        'stdout':    _cap_out.getvalue(),
        'stderr':    _cap_err.getvalue(),
        'exit_code': _exit,
        'time_ms':   round(_elapsed, 2),
        'tle':       _tle_flag,
    }})

sys.stdout.write(json.dumps(_results))
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
# BATCH SUBPROCESS — runs ALL test cases in ONE process
# ~20ms total overhead vs N×20ms for N separate processes
# ═══════════════════════════════════════════════════════════════

def _run_batch_subprocess(code: str, inputs: list,
                          time_limit_sec: float = 2.0) -> list:
    """Run user code against multiple inputs in a single subprocess.
    Returns a list of ExecutionResult, one per input."""
    code_b64 = base64.b64encode(code.encode('utf-8')).decode('ascii')

    env = os.environ.copy()
    env['USER_CODE']   = code_b64
    env['USER_INPUTS'] = _json.dumps(inputs)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'

    # Total wall-clock = sum of per-test limits + buffer
    total_timeout = (time_limit_sec * len(inputs)) + 5

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, '-u', '-c', WRAPPER_BATCH],
            env=env,
            capture_output=True,
            timeout=total_timeout,
            text=True,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        if proc.returncode != 0:
            # Entire batch failed (syntax error etc)
            return [
                ExecutionResult(
                    stderr=proc.stderr or 'Execution failed',
                    exit_code=proc.returncode,
                    execution_time_ms=elapsed_ms / max(len(inputs), 1),
                )
                for _ in inputs
            ]

        try:
            raw_results = _json.loads(proc.stdout)
        except _json.JSONDecodeError:
            return [
                ExecutionResult(
                    stderr='Failed to parse batch output',
                    exit_code=1,
                    execution_time_ms=elapsed_ms / max(len(inputs), 1),
                )
                for _ in inputs
            ]

        results = []
        for r in raw_results:
            mle = (
                'MemoryError' in r.get('stdout', '')
                or 'MemoryError' in r.get('stderr', '')
            )
            tle = r.get('time_ms', 0) > (time_limit_sec * 1000 + 500)
            results.append(ExecutionResult(
                stdout=r.get('stdout', ''),
                stderr=r.get('stderr', ''),
                exit_code=r.get('exit_code', 0),
                execution_time_ms=r.get('time_ms', 0),
                tle=tle,
                mle=mle,
            ))
        return results

    except subprocess.TimeoutExpired:
        elapsed_ms = (time.monotonic() - start) * 1000
        return [
            ExecutionResult(
                stderr='Time Limit Exceeded (batch)',
                exit_code=124,
                execution_time_ms=elapsed_ms / max(len(inputs), 1),
                tle=True,
            )
            for _ in inputs
        ]
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        return [
            ExecutionResult(
                stderr=f'Execution error: {type(e).__name__}: {e}',
                exit_code=1,
                execution_time_ms=elapsed_ms / max(len(inputs), 1),
            )
            for _ in inputs
        ]


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
# BATCH DOCKER — runs ALL test cases in ONE container
# ═══════════════════════════════════════════════════════════════

def _run_batch_docker(code: str, inputs: list,
                      time_limit_sec: float = 2.0) -> list:
    """Run user code against multiple inputs in a single Docker container."""
    try:
        import docker as docker_lib
        client = docker_lib.from_env(timeout=30)
    except Exception as e:
        return [
            ExecutionResult(stderr=f'Docker unavailable: {e}', exit_code=1)
            for _ in inputs
        ]

    code_b64 = base64.b64encode(code.encode('utf-8')).decode('ascii')
    mem_bytes = 128 * 1024 * 1024
    bootstrap = WRAPPER_DOCKER_BATCH.format(
        time_limit=max(int(time_limit_sec) + 2, 5),
        mem=mem_bytes,
    )

    total_timeout = (time_limit_sec * len(inputs)) + 10
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
                'USER_CODE':   code_b64,
                'USER_INPUTS': _json.dumps(inputs),
            },
        )

        # Wait for container
        startup_deadline = time.monotonic() + 20
        while time.monotonic() < startup_deadline:
            container.reload()
            if container.status in ('running', 'exited', 'dead'):
                break
            time.sleep(0.05)

        exec_start = time.monotonic()
        timed_out = False
        try:
            exit_result = container.wait(timeout=total_timeout)
            exit_code = exit_result.get('StatusCode', -1)
        except Exception:
            timed_out = True
            exit_code = 124
            try:
                container.kill()
            except Exception:
                pass

        elapsed_ms = (time.monotonic() - exec_start) * 1000

        try:
            stdout_raw = container.logs(stdout=True, stderr=False)
            stderr_raw = container.logs(stdout=False, stderr=True)
        except Exception:
            stdout_raw = b''
            stderr_raw = b''

        stdout_str = stdout_raw.decode('utf-8', errors='replace')
        stderr_str = stderr_raw.decode('utf-8', errors='replace')

        if timed_out or exit_code != 0:
            return [
                ExecutionResult(
                    stderr=stderr_str or 'Execution failed',
                    exit_code=exit_code,
                    execution_time_ms=elapsed_ms / max(len(inputs), 1),
                    tle=timed_out,
                )
                for _ in inputs
            ]

        try:
            raw_results = _json.loads(stdout_str)
        except _json.JSONDecodeError:
            return [
                ExecutionResult(
                    stderr='Failed to parse batch output',
                    exit_code=1,
                    execution_time_ms=elapsed_ms / max(len(inputs), 1),
                )
                for _ in inputs
            ]

        results = []
        for r in raw_results:
            tle = r.get('tle', False) or r.get('time_ms', 0) > (time_limit_sec * 1000 + 500)
            mle = (
                'MemoryError' in r.get('stdout', '')
                or 'MemoryError' in r.get('stderr', '')
            )
            results.append(ExecutionResult(
                stdout=r.get('stdout', ''),
                stderr=r.get('stderr', ''),
                exit_code=r.get('exit_code', 0),
                execution_time_ms=r.get('time_ms', 0),
                tle=tle,
                mle=mle,
            ))
        return results

    except Exception as e:
        return [
            ExecutionResult(
                stderr=f'Execution error: {type(e).__name__}: {e}',
                exit_code=1,
            )
            for _ in inputs
        ]
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# JAVA EXECUTION — helpers
# ═══════════════════════════════════════════════════════════════

import re

def _extract_java_class_name(code: str) -> str:
    """Extract the public class name from Java source code.
    Falls back to 'Solution' if no public class is found."""
    match = re.search(r'public\s+class\s+(\w+)', code)
    return match.group(1) if match else 'Solution'


# ═══════════════════════════════════════════════════════════════
# JAVA EXECUTION — subprocess (local dev)
# Compile .java → run with java
# ═══════════════════════════════════════════════════════════════

def _run_java_subprocess(code: str, stdin_data: str,
                         time_limit_sec: float = 2.0) -> ExecutionResult:
    """Run user Java code in a subprocess with timeout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write the user's Java file — filename must match public class name
        class_name = _extract_java_class_name(code)
        java_file = os.path.join(tmpdir, f'{class_name}.java')
        with open(java_file, 'w') as f:
            f.write(code)

        # ── Compile ──
        start = time.monotonic()
        try:
            compile_proc = subprocess.run(
                ['javac', java_file],
                capture_output=True,
                timeout=15,
                text=True,
                cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                stderr='Compilation timed out',
                exit_code=1,
                execution_time_ms=(time.monotonic() - start) * 1000,
            )
        except FileNotFoundError:
            return ExecutionResult(
                stderr='Java compiler (javac) not found. Please install JDK.',
                exit_code=1,
            )

        if compile_proc.returncode != 0:
            return ExecutionResult(
                stderr=compile_proc.stderr,
                exit_code=compile_proc.returncode,
                execution_time_ms=(time.monotonic() - start) * 1000,
            )

        # ── Run ──
        run_start = time.monotonic()
        try:
            proc = subprocess.run(
                ['java', '-cp', tmpdir, class_name],
                input=stdin_data or '',
                capture_output=True,
                timeout=time_limit_sec + 2,
                text=True,
            )
            elapsed_ms = (time.monotonic() - run_start) * 1000
            tle = elapsed_ms > (time_limit_sec * 1000 + 500)
            mle = (
                'OutOfMemoryError' in proc.stdout
                or 'OutOfMemoryError' in proc.stderr
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
            elapsed_ms = (time.monotonic() - run_start) * 1000
            return ExecutionResult(
                stderr='Time Limit Exceeded',
                exit_code=124,
                execution_time_ms=elapsed_ms,
                tle=True,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - run_start) * 1000
            return ExecutionResult(
                stderr=f'Execution error: {type(e).__name__}: {e}',
                exit_code=1,
                execution_time_ms=elapsed_ms,
            )


def _run_batch_java_subprocess(code: str, inputs: list,
                               time_limit_sec: float = 2.0) -> list:
    """Run user Java code against multiple inputs. Compile once, run N times."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write the user's Java file — filename must match public class name
        class_name = _extract_java_class_name(code)
        java_file = os.path.join(tmpdir, f'{class_name}.java')
        with open(java_file, 'w') as f:
            f.write(code)

        # ── Compile ──
        try:
            compile_proc = subprocess.run(
                ['javac', java_file],
                capture_output=True,
                timeout=15,
                text=True,
                cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            return [
                ExecutionResult(stderr='Compilation timed out', exit_code=1)
                for _ in inputs
            ]
        except FileNotFoundError:
            return [
                ExecutionResult(
                    stderr='Java compiler (javac) not found. Please install JDK.',
                    exit_code=1,
                )
                for _ in inputs
            ]

        if compile_proc.returncode != 0:
            return [
                ExecutionResult(
                    stderr=compile_proc.stderr,
                    exit_code=compile_proc.returncode,
                )
                for _ in inputs
            ]

        # ── Run each test case ──
        results = []
        for inp in inputs:
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    ['java', '-cp', tmpdir, class_name],
                    input=inp or '',
                    capture_output=True,
                    timeout=time_limit_sec + 2,
                    text=True,
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                tle = elapsed_ms > (time_limit_sec * 1000 + 500)
                mle = (
                    'OutOfMemoryError' in proc.stdout
                    or 'OutOfMemoryError' in proc.stderr
                )
                results.append(ExecutionResult(
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    exit_code=proc.returncode,
                    execution_time_ms=elapsed_ms,
                    tle=tle,
                    mle=mle,
                ))
            except subprocess.TimeoutExpired:
                elapsed_ms = (time.monotonic() - start) * 1000
                results.append(ExecutionResult(
                    stderr='Time Limit Exceeded',
                    exit_code=124,
                    execution_time_ms=elapsed_ms,
                    tle=True,
                ))
            except Exception as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                results.append(ExecutionResult(
                    stderr=f'Execution error: {type(e).__name__}: {e}',
                    exit_code=1,
                    execution_time_ms=elapsed_ms,
                ))

        return results


# ═══════════════════════════════════════════════════════════════
# JAVA EXECUTION — Docker (production)
# ═══════════════════════════════════════════════════════════════

def _run_java_docker(code: str, stdin_data: str,
                     time_limit_sec: float = 2.0) -> ExecutionResult:
    """Run user Java code in a sandboxed Docker container."""
    try:
        import docker as docker_lib
        client = docker_lib.from_env(timeout=30)
    except Exception as e:
        return ExecutionResult(stderr=f'Docker unavailable: {e}', exit_code=1)

    # Write Java code to a temp file, mount it into container
    with tempfile.TemporaryDirectory() as tmpdir:
        class_name = _extract_java_class_name(code)
        java_file = os.path.join(tmpdir, f'{class_name}.java')
        with open(java_file, 'w') as f:
            f.write(code)

        stdin_file = os.path.join(tmpdir, 'input.txt')
        with open(stdin_file, 'w') as f:
            f.write(stdin_data or '')

        # Shell command: compile and run
        shell_cmd = (
            f'cd /workspace && javac {class_name}.java 2>&1 && '
            f'timeout {int(time_limit_sec) + 2} java -cp /workspace {class_name} < /workspace/input.txt'
        )

        container = None
        try:
            container = client.containers.run(
                image=JAVA_IMAGE,
                command=['sh', '-c', shell_cmd],
                network_disabled=True,
                mem_limit=MEMORY_LIMIT,
                memswap_limit=MEMORY_LIMIT,
                cpu_quota=CPU_QUOTA,
                pids_limit=64,
                security_opt=['no-new-privileges'],
                cap_drop=['ALL'],
                detach=True,
                remove=False,
                volumes={tmpdir: {'bind': '/workspace', 'mode': 'rw'}},
            )

            exec_start = time.monotonic()
            wall_limit = time_limit_sec + 10
            timed_out = False
            try:
                exit_result = container.wait(timeout=wall_limit)
                exit_code = exit_result.get('StatusCode', -1)
            except Exception:
                timed_out = True
                exit_code = 124
                try:
                    container.kill()
                except Exception:
                    pass

            elapsed_ms = (time.monotonic() - exec_start) * 1000

            try:
                stdout_raw = container.logs(stdout=True, stderr=False)
                stderr_raw = container.logs(stdout=False, stderr=True)
            except Exception:
                stdout_raw = b''
                stderr_raw = b''

            stdout_str = stdout_raw.decode('utf-8', errors='replace')
            stderr_str = stderr_raw.decode('utf-8', errors='replace')

            tle = timed_out or exit_code == 124 or elapsed_ms > (time_limit_sec * 1000 + 500)
            mle = 'OutOfMemoryError' in stdout_str or 'OutOfMemoryError' in stderr_str

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


def _run_batch_java_docker(code: str, inputs: list,
                           time_limit_sec: float = 2.0) -> list:
    """Run user Java code against multiple inputs in Docker. Compile once, run N times."""
    try:
        import docker as docker_lib
        client = docker_lib.from_env(timeout=30)
    except Exception as e:
        return [
            ExecutionResult(stderr=f'Docker unavailable: {e}', exit_code=1)
            for _ in inputs
        ]

    with tempfile.TemporaryDirectory() as tmpdir:
        class_name = _extract_java_class_name(code)
        java_file = os.path.join(tmpdir, f'{class_name}.java')
        with open(java_file, 'w') as f:
            f.write(code)

        # Write all inputs as separate files
        for i, inp in enumerate(inputs):
            inp_file = os.path.join(tmpdir, f'input_{i}.txt')
            with open(inp_file, 'w') as f:
                f.write(inp or '')

        # Build shell script to compile once and run each input
        run_cmds = []
        for i in range(len(inputs)):
            run_cmds.append(
                f'echo "---RESULT_START_{i}---" && '
                f'START=$(date +%s%N) && '
                f'timeout {int(time_limit_sec) + 2} java -cp /workspace {class_name} < /workspace/input_{i}.txt 2>/workspace/err_{i}.txt; '
                f'EC=$?; END=$(date +%s%N); '
                f'echo "---RESULT_META_{i}---"; '
                f'echo "exit:$EC"; '
                f'ELAPSED=$(( (END - START) / 1000000 )); '
                f'echo "time:$ELAPSED"; '
                f'echo "---RESULT_ERR_{i}---"; '
                f'cat /workspace/err_{i}.txt 2>/dev/null; '
                f'echo "---RESULT_END_{i}---"'
            )

        shell_cmd = (
            f'cd /workspace && javac {class_name}.java 2>&1; '
            f'if [ $? -ne 0 ]; then echo "COMPILE_ERROR"; exit 1; fi; '
            + ' && '.join(run_cmds)
        )

        total_timeout = (time_limit_sec * len(inputs)) + 15
        container = None
        try:
            container = client.containers.run(
                image=JAVA_IMAGE,
                command=['sh', '-c', shell_cmd],
                network_disabled=True,
                mem_limit=MEMORY_LIMIT,
                memswap_limit=MEMORY_LIMIT,
                cpu_quota=CPU_QUOTA,
                pids_limit=64,
                security_opt=['no-new-privileges'],
                cap_drop=['ALL'],
                detach=True,
                remove=False,
                volumes={tmpdir: {'bind': '/workspace', 'mode': 'rw'}},
            )

            exec_start = time.monotonic()
            timed_out = False
            try:
                exit_result = container.wait(timeout=total_timeout)
                exit_code = exit_result.get('StatusCode', -1)
            except Exception:
                timed_out = True
                exit_code = 124
                try:
                    container.kill()
                except Exception:
                    pass

            elapsed_ms = (time.monotonic() - exec_start) * 1000

            try:
                stdout_raw = container.logs(stdout=True, stderr=False)
            except Exception:
                stdout_raw = b''

            stdout_str = stdout_raw.decode('utf-8', errors='replace')

            # Check for compile error
            if 'COMPILE_ERROR' in stdout_str or exit_code != 0:
                compile_err = stdout_str.replace('COMPILE_ERROR', '').strip()
                return [
                    ExecutionResult(
                        stderr=compile_err or 'Compilation failed',
                        exit_code=1,
                    )
                    for _ in inputs
                ]

            # Parse individual results
            results = []
            for i in range(len(inputs)):
                start_marker = f'---RESULT_START_{i}---'
                meta_marker = f'---RESULT_META_{i}---'
                err_marker = f'---RESULT_ERR_{i}---'
                end_marker = f'---RESULT_END_{i}---'

                try:
                    output_section = stdout_str.split(start_marker)[1].split(meta_marker)[0].strip()
                    meta_section = stdout_str.split(meta_marker)[1].split(err_marker)[0].strip()
                    err_section = stdout_str.split(err_marker)[1].split(end_marker)[0].strip()

                    tc_exit = 0
                    tc_time = 0.0
                    for line in meta_section.split('\n'):
                        if line.startswith('exit:'):
                            tc_exit = int(line.split(':')[1])
                        elif line.startswith('time:'):
                            tc_time = float(line.split(':')[1])

                    tle = tc_exit == 124 or tc_time > (time_limit_sec * 1000 + 500)
                    mle = 'OutOfMemoryError' in output_section or 'OutOfMemoryError' in err_section

                    results.append(ExecutionResult(
                        stdout=output_section,
                        stderr=err_section,
                        exit_code=tc_exit,
                        execution_time_ms=tc_time,
                        tle=tle,
                        mle=mle,
                    ))
                except (IndexError, ValueError):
                    results.append(ExecutionResult(
                        stderr='Failed to parse result',
                        exit_code=1,
                        execution_time_ms=elapsed_ms / max(len(inputs), 1),
                    ))

            return results

        except Exception as e:
            return [
                ExecutionResult(
                    stderr=f'Execution error: {type(e).__name__}: {e}',
                    exit_code=1,
                )
                for _ in inputs
            ]
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════
# PUBLIC API — auto-selects backend + language
# ═══════════════════════════════════════════════════════════════

def run_code(code: str, stdin_data: str,
             time_limit_sec: float = 2.0,
             language: str = 'python') -> ExecutionResult:
    """
    Run user code with the appropriate backend and language.

    - Local dev  → subprocess (~20ms/test, no Docker required)
    - Production → Docker    (full sandbox isolation)

    Controlled by settings.USE_DOCKER_EXECUTOR (default: False).
    Set USE_DOCKER_EXECUTOR=True in .env.prod or settings_prod.py.
    """
    if language == 'java':
        if USE_DOCKER:
            return _run_java_docker(code, stdin_data, time_limit_sec)
        return _run_java_subprocess(code, stdin_data, time_limit_sec)
    else:
        if USE_DOCKER:
            return _run_docker(code, stdin_data, time_limit_sec)
        return _run_subprocess(code, stdin_data, time_limit_sec)


def run_code_batch(code: str, inputs: list,
                   time_limit_sec: float = 2.0,
                   language: str = 'python') -> list:
    """
    Run user code against MULTIPLE inputs in a SINGLE process/container.

    This is dramatically faster than calling run_code() N times because:
    - Subprocess: 1 process start vs N process starts (~20ms vs N×20ms)
    - Docker:     1 container start vs N container starts (~200ms vs N×200ms)

    Returns a list of ExecutionResult, one per input.
    """
    if not inputs:
        return []
    if language == 'java':
        if USE_DOCKER:
            return _run_batch_java_docker(code, inputs, time_limit_sec)
        return _run_batch_java_subprocess(code, inputs, time_limit_sec)
    else:
        if USE_DOCKER:
            return _run_batch_docker(code, inputs, time_limit_sec)
        return _run_batch_subprocess(code, inputs, time_limit_sec)