"""Standard-library progress, exclusive setup lock, and logged subprocess execution."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit


def activate_local_tools():
    """Use tools from the offline installer without changing shell/system PATH settings."""
    candidates = []
    if os.environ.get('CANTONESE_SUBTITLES_TOOLS'):
        candidates.append(Path(os.environ['CANTONESE_SUBTITLES_TOOLS']))
    record = Path.home()/'.config/cantonese-subtitles/environment.local.json'
    try:
        saved = json.loads(record.read_text())
        if saved.get('tools_bin'):
            candidates.append(Path(saved['tools_bin']))
    except (OSError, ValueError, TypeError):
        pass
    for directory in candidates:
        if all((directory/name).is_file() and os.access(directory/name, os.X_OK) for name in ('ffmpeg', 'ffprobe')):
            current = os.environ.get('PATH', os.defpath).split(os.pathsep)
            os.environ['PATH'] = os.pathsep.join([str(directory)]+[p for p in current if p != str(directory)])
            return directory
    return None


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=path.name+'.',
                                         suffix='.tmp', delete=False, encoding='utf-8') as f:
            temp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if temp and temp.exists():
            temp.unlink()


class Progress:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            'schema_version': 1, 'steps': [], 'artifacts': [], 'review_items': []}

    def set(self, step, status, detail='', **fields):
        row = next((x for x in self.data['steps'] if x['id'] == step), None)
        if row is None:
            row = {'id': step}
            self.data['steps'].append(row)
        if status == 'running' and row.get('status') != 'running':
            row.update(started_at=now(), finished_at=None)
        row.update(status=status, detail=detail, **fields)
        if status in ('passed', 'failed', 'review'):
            row['finished_at'] = now()
        self.data['updated_at'] = now()
        atomic_json(self.path, self.data)
        marks = {'running': '🔄', 'passed': '✅', 'failed': '❌', 'review': '⚠️',
                 'waiting': '⏸️', 'pending': '⬜'}
        print(f'{marks.get(status, "·")} {step}: {detail}', flush=True)


class SetupLock:
    def __init__(self, path):
        self.path = Path(path)
        self.owned = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Never delete a lock automatically: a PID may belong to another
            # machine/session. The operator must first verify the old task ended.
            raise RuntimeError(f'已有安装锁 {self.path}；请先核对原任务，不要重复安装')
        self.owned = True
        with os.fdopen(fd, 'w') as f:
            json.dump({'pid': os.getpid(), 'started_at': now()}, f)
        return self

    def __exit__(self, *args):
        if self.owned:
            self.path.unlink(missing_ok=True)


def index_url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('下载源必须是无凭据、无查询参数的HTTPS索引地址')
    return value.rstrip('/')


def classify_error(text):
    s = text.lower()
    if 'metadata_errors' in s and 'unexpected keyword' in s:
        return 'pyav_incompatible'
    if 'eexist' in s or 'file already exists' in s:
        return 'temporary_directory_conflict'
    if any(t in s for t in ['permission denied', 'operation not permitted', 'access is denied']):
        return 'permission_denied'  # Not proof of security-software interception.
    if any(t in s for t in ['no matching distribution', 'could not find a version']):
        return 'distribution_unavailable'
    if any(t in s for t in ['timed out', 'connectionerror', 'connection reset',
                             'failed to establish', 'temporary failure in name resolution']):
        return 'network'
    return 'unclassified'


def stop_process(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def run_logged(command, progress, step, log_dir, *, env=None, timeout=1200, heartbeat=30):
    """Stream real output; bounded wait; no hidden retries or shell interpolation."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f'{step}-{time.time_ns()}.log'
    proc = subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding='utf-8', errors='replace', env=env,
                            start_new_session=(os.name == 'posix'))
    messages = queue.Queue()

    def read():
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            proc.stdout.close()
            messages.put(None)

    threading.Thread(target=read, daemon=True).start()
    started = last = time.monotonic()
    tail = ''
    progress.set(step, 'running', '任务已启动 尚未验证完成', task_id=proc.pid,
                 log_path=str(log_path), exit_code=None, error=None,
                 command=[re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', str(x)) for x in command])
    try:
        with log_path.open('w', encoding='utf-8') as log:
            while True:
                if time.monotonic()-started > timeout:
                    raise TimeoutError(f'此步骤超过{timeout}秒；已结束本次子进程，请检查日志')
                try:
                    line = messages.get(timeout=.5)
                except queue.Empty:
                    line = ''
                if line is None:
                    break
                if line:
                    # Strip credentials if a package manager prints an authenticated URL.
                    line = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', line)
                    log.write(line)
                    log.flush()
                    tail = (tail+line)[-24000:]
                    print(line.rstrip(), flush=True)
                if time.monotonic()-last >= heartbeat:
                    last = time.monotonic()
                    recent = tail.splitlines()[-1][:250] if tail else '暂无新输出 此步骤进度未知'
                    progress.set(step, 'running', f'已耗时{int(last-started)}秒 最近输出：{recent}',
                                 elapsed_seconds=int(last-started))
            code = proc.wait(timeout=10)
        if code:
            kind = classify_error(tail)
            progress.set(step, 'failed', f'退出码{code} 原因分类：{kind} 详见日志',
                         exit_code=code, error={'category': kind, 'tail': tail[-5000:]})
            raise RuntimeError(f'{step}: {kind}; 日志 {log_path}')
        progress.set(step, 'passed', '命令执行结束；运行能力由后续自检验证', exit_code=0)
        return log_path
    except BaseException as e:
        stop_process(proc)
        if isinstance(e, TimeoutError):
            progress.set(step, 'failed', str(e), error={'category': 'timeout'}, exit_code=proc.returncode)
        elif isinstance(e, KeyboardInterrupt):
            progress.set(step, 'waiting', '用户中断 子进程已停止 可核对状态后恢复')
        raise
