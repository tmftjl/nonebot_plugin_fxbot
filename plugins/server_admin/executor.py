"""有界 shell 任务、按主人表态的一次性确认、进程清理及逐条执行审计。"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import signal
import asyncio
from uuid import uuid4
from pathlib import Path
from dataclasses import field, dataclass
from collections.abc import Callable

from nonebot import logger

from ...utils.tz import now
from ...utils.paths import data_dir

COMMAND_TIMEOUT = 60.0
CONFIRM_TTL = 120.0
OUTPUT_LIMIT = 4000
MAX_COMMAND_CHARS = 12000
_READ_COMMANDS = {"pwd", "ls", "dir", "whoami", "hostname", "uname", "uptime", "date", "df", "free", "echo"}


def needs_confirmation(command: str) -> bool:
    """仅简单查询自动执行，其余保守确认；此判定不是安全沙箱。"""
    value = command.strip()
    if re.search(r"[\n\r;&|><`$()%{}]", value):
        return True
    first = value.split(None, 1)[0].casefold() if value else ""
    return first not in _READ_COMMANDS


def audit(action: str, session_id: str, **details) -> None:
    """执行前审计失败即不启动，日志限量轮转且不保存命令输出。"""
    path = data_dir("server_admin") / "audit.jsonl"
    if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
        for index in range(2, 0, -1):
            old = path.with_suffix(f".jsonl.{index}")
            if old.exists():
                old.replace(path.with_suffix(f".jsonl.{index + 1}"))
        path.replace(path.with_suffix(".jsonl.1"))
    record = {"time": now().isoformat(), "action": action, "session_id": session_id, **details}
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()


@dataclass
class Command:
    """一条不可变执行意图的运行状态，确认编号同执行任务编号。"""

    session_id: str
    command: str
    cwd: str
    authorize: Callable[[], bool] = field(repr=False)
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    created: float = field(default_factory=time.monotonic)
    status: str = "pending"
    shown: bool = False
    source: str = field(default="", repr=False)
    output: str = ""
    truncated: bool = False
    returncode: int | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def result(self) -> dict:
        """返回有界输出，不把执行上下文或权限闭包交给模型。"""
        return {
            "job_id": self.id,
            "status": self.status,
            "command": self.command,
            "cwd": self.cwd,
            "output": self.output,
            "truncated": self.truncated,
            "returncode": self.returncode,
        }


class CommandExecutor:
    """一个服务器会话只允许一条待确认或运行命令，全进程至多四条。"""

    def __init__(self) -> None:
        self.jobs: dict[str, Command] = {}
        self.stopping = False

    def _prune(self) -> None:
        """主动清理过期待确认记录及历史输出，不无限保留敏感命令。"""
        for job in list(self.jobs.values()):
            if job.status == "pending" and time.monotonic() - job.created >= CONFIRM_TTL:
                job.status = "expired"
                audit("expired", job.session_id, job_id=job.id)
        finished = [job for job in self.jobs.values() if job.status not in {"pending", "running"}]
        for job in finished[:-100]:
            self.jobs.pop(job.id, None)

    def submit(self, session_id: str, command: str, cwd: str, authorize: Callable[[], bool]) -> dict:
        """保存确切命令后返回确认请求或启动任务，启动前再次校验权限。"""
        self._prune()
        if self.stopping or not authorize():
            raise PermissionError("执行权限不足")
        if not command.strip() or len(command) > MAX_COMMAND_CHARS or "\x00" in command:
            raise ValueError("命令不能为空、包含空字节或超过 12000 字")
        if cwd and not Path(cwd).is_absolute():
            raise ValueError("工作目录必须为绝对路径")
        directory = Path(cwd or os.getcwd()).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("工作目录无效")
        active = [job for job in self.jobs.values() if job.status in {"pending", "running"}]
        if any(job.session_id == session_id for job in active) or len(active) >= 4:
            raise ValueError("已有待确认或运行中的任务，请先查询、确认或取消")
        job = Command(session_id, command, str(directory), authorize)
        audit("submitted", session_id, job_id=job.id, command=command, cwd=str(directory))
        self.jobs[job.id] = job
        if not needs_confirmation(command):
            self._start(job)
        return job.result()

    def _start(self, job: Command) -> None:
        """同步消费待确认状态，确保并发确认最多执行一次。"""
        if job.status != "pending" or not job.authorize() or self.stopping:
            raise PermissionError("确认已失效")
        audit("execution_start", job.session_id, job_id=job.id, command=job.command, cwd=job.cwd)
        job.status = "running"
        job.task = asyncio.create_task(self._run(job), name=f"server-admin-{job.id}")

    def confirm(self, session_id: str, source: str, reply: str = "") -> dict:
        """只能由主人随后一轮明确表态执行；同一事件里的自我确认无效。"""
        job = self._pending(session_id)
        if job is None or not source or job.source == source:
            raise ValueError("没有匹配的待确认命令，或确认已过期")
        audit("confirmed", session_id, job_id=job.id, reply=reply[:500])
        self._start(job)
        return job.result()

    def _pending(self, session_id: str) -> Command | None:
        """本会话唯一已展示且未过期的待确认任务。"""
        self._prune()
        jobs = [
            job for job in self.jobs.values() if job.session_id == session_id and job.status == "pending" and job.shown
        ]
        return jobs[0] if len(jobs) == 1 else None

    def pending(self, session_id: str) -> dict | None:
        """返回待确认任务的公开字段，供提示词告诉模型当前在等哪条命令。"""
        job = self._pending(session_id)
        return job.result() if job else None

    def presented(self, session_id: str, identifier: str, source: str = "") -> None:
        """只有确认消息成功发送后才开放确认，并记下提交它的事件。"""
        job = self.jobs.get(identifier)
        if job is not None and job.session_id == session_id and job.status == "pending":
            job.shown = True
            job.source = source
            audit("confirmation_presented", session_id, job_id=identifier)

    def query(self, session_id: str, identifier: str) -> dict:
        """只允许查看自己会话的任务。"""
        self._prune()
        job = self.jobs.get(identifier)
        if job is None or job.session_id != session_id:
            raise ValueError("任务不存在或不属于当前会话")
        if not job.authorize():
            self.cancel(session_id)
            raise PermissionError("执行权限不足")
        return job.result()

    def cancel(self, session_id: str) -> bool:
        """撤销所有该会话待确认任务，并取消在途 shell。"""
        changed = False
        for job in self.jobs.values():
            if job.session_id != session_id or job.status not in {"pending", "running"}:
                continue
            if job.task is not None:
                job.task.cancel()
            job.status = "cancelled"
            changed = True
            try:
                audit("cancelled", session_id, job_id=job.id)
            except Exception:
                logger.opt(exception=True).error("[ServerAdmin] 取消审计失败，仍停止进程")
        return changed

    async def stop(self) -> None:
        """停机撤销确认并等所有子进程清理完毕。"""
        self.stopping = True
        for session_id in {job.session_id for job in self.jobs.values()}:
            self.cancel(session_id)
        await asyncio.gather(*(job.task for job in self.jobs.values() if job.task is not None), return_exceptions=True)

    async def _run(self, job: Command) -> None:
        """流式排空输出并限量保留，超时、撤权和取消都清理整个进程组。"""
        process = None
        windows_job = None
        readers: list[asyncio.Task] = []
        try:
            if not job.authorize():
                job.status = "cancelled"
                return
            if os.name == "nt":
                from .windows_job import WindowsJob

                windows_job = WindowsJob()
                # 子进程先等待 stdin 握手，纳入 Job 后才允许执行 shell，避免派生进程竞态。
                script = (
                    "import sys,subprocess; sys.stdin.buffer.read(1); sys.exit(subprocess.call(sys.argv[1],shell=True))"
                )
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    script,
                    job.command,
                    cwd=job.cwd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=0x08000000,
                )
                windows_job.assign(process.pid)
                process.stdin.write(b"1")
                await process.stdin.drain()
                process.stdin.close()
            else:
                process = await asyncio.create_subprocess_shell(
                    job.command,
                    cwd=job.cwd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )

            async def read_output() -> None:
                """按字节限量保留但始终排空管道，避免子进程阻塞和无限内存。"""
                collected = bytearray()
                while chunk := await process.stdout.read(4096):
                    available = max(0, OUTPUT_LIMIT * 4 - len(collected))
                    collected.extend(chunk[:available])
                    if len(chunk) > available:
                        job.truncated = True
                    text = collected.decode("utf-8", errors="replace")
                    job.truncated = job.truncated or len(text) > OUTPUT_LIMIT
                    job.output = text[:OUTPUT_LIMIT]

            async def watch_permission() -> None:
                """运行期间持续重查开关与主人身份。"""
                while True:
                    if not job.authorize() or self.stopping:
                        raise PermissionError("运行权限已撤销")
                    await asyncio.sleep(0.5)

            reader = asyncio.create_task(read_output())
            waiter = asyncio.create_task(process.wait())
            watcher = asyncio.create_task(watch_permission())
            readers = [reader, waiter, watcher]

            async def collect_result() -> None:
                """shell 与管道都结束才算完成，后台子进程占用管道期间也持续检查撤权。"""
                job.returncode = await waiter
                await reader

            collector = asyncio.create_task(collect_result())
            readers.append(collector)

            async def wait_command() -> None:
                """等待命令与输出完成，同时允许撤权提前打断。"""
                completed, _ = await asyncio.wait({collector, watcher}, return_when=asyncio.FIRST_COMPLETED)
                for task in completed:
                    task.result()
                await collector

            await asyncio.wait_for(wait_command(), COMMAND_TIMEOUT)
            job.status = "completed"
        except TimeoutError:
            job.status = "timeout"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except PermissionError:
            job.status = "cancelled"
        except Exception:
            job.status = "failed"
            logger.opt(exception=True).warning("[ServerAdmin] 命令任务失败")
        finally:
            if windows_job is not None:
                windows_job.close()
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for task in readers:
                task.cancel()
            if process is not None:
                await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
            try:
                audit("execution_end", job.session_id, job_id=job.id, status=job.status, returncode=job.returncode)
            except Exception:
                logger.opt(exception=True).error("[ServerAdmin] 执行终态审计失败")


executor = CommandExecutor()
