"""
Safe execution sandbox for running student binaries with strict timeout,
isolated environments, process-tree killing, and crash diagnostics.

Linux (ELF) binaries produced by the WSL backend are executed inside WSL with coreutils `timeout`
and resource limits; native binaries run directly. On Windows 11, Smart App Control can block
freshly compiled unsigned .exe files (WinError 4551) - that is reported as its own error type so
the pipeline can transparently rebuild the program in WSL instead.
"""

from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import time
import sys
from pathlib import Path
from typing import Optional, Dict, List

from grader.models import ExecutionResult, CheckStatus

MAX_OUTPUT_CHARS = 64_000
BLOCKED_ERROR_TYPES = ("BLOCKED_BY_SMART_APP_CONTROL", "BLOCKED_BY_ANTIVIRUS", "OS_LAUNCH_BLOCKED")

# Resource limits applied inside WSL: 2 GiB address space, 50 MiB max file size, 256 processes.
# `timeout` is not exec'd so a signal death comes back as 128+N (139 = SIGSEGV), distinguishable from exit(11).
_WSL_RUN_SCRIPT = (
    'ulimit -v 2097152 2>/dev/null; ulimit -f 102400 2>/dev/null; ulimit -u 256 2>/dev/null; '
    'cd "$1" || exit 125; timeout -k 1 "$2" "$3"; exit $?'
)


def is_elf_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


class SandboxRunner:
    """Safely runs compiled student binaries."""

    @staticmethod
    def run_executable(
        executable_path: str | Path,
        input_data: str = "",
        timeout_sec: float = 2.0,
        working_dir: Optional[Path] = None
    ) -> ExecutionResult:
        """
        Executes a binary with stdin feeding and timeout protection.
        Guarantees process tree termination on timeout or crash.
        """
        exe_path = Path(executable_path).resolve()
        if not exe_path.exists():
            return ExecutionResult(
                status=CheckStatus.RUNTIME_ERROR,
                returncode=-1,
                stderr=f"Executable not found: {exe_path}. It may have been quarantined by antivirus.",
                error_type="MISSING_EXECUTABLE"
            )

        created_cwd = working_dir is None
        temp_cwd = Path(working_dir) if working_dir else Path(tempfile.mkdtemp(prefix="run_sandbox_"))
        temp_cwd.mkdir(parents=True, exist_ok=True)

        clean_input = (input_data or "").replace("\r\n", "\n")
        if clean_input and not clean_input.endswith("\n"):
            clean_input += "\n"

        try:
            if sys.platform == "win32" and is_elf_binary(exe_path):
                return SandboxRunner._run_in_wsl(exe_path, clean_input, timeout_sec, temp_cwd)
            return SandboxRunner._run_native(exe_path, clean_input, timeout_sec, temp_cwd)
        finally:
            if created_cwd:
                shutil.rmtree(temp_cwd, ignore_errors=True)

    @staticmethod
    def warm_up(executable_path: str | Path) -> None:
        """
        Launches a fresh native binary once with empty stdin and discards the result.
        The first launch of a new .exe is when antivirus scans it (often 1-3 s), which used to eat
        the whole time budget of the first test case and show up as a bogus TIMEOUT.
        """
        exe_path = Path(executable_path)
        if sys.platform != "win32" or not exe_path.exists() or is_elf_binary(exe_path):
            return
        SandboxRunner.run_executable(exe_path, input_data="", timeout_sec=3.0)

    # ------------------------------------------------------------------ native

    @staticmethod
    def _sanitized_env() -> Dict[str, str]:
        clean_env = os.environ.copy()
        for key in list(clean_env):
            if any(tag in key.upper() for tag in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                clean_env.pop(key, None)
        # Include compiler runtime directory in PATH so MinGW DLLs (libgcc, libwinpthread) are found
        from grader.config import SystemConfig
        from grader.models import LanguageType
        compiler_bin, _ = SystemConfig.find_compiler(LanguageType.C)
        if compiler_bin:
            clean_env["PATH"] = str(Path(compiler_bin).parent.resolve()) + os.pathsep + clean_env.get("PATH", "")
        return clean_env

    @staticmethod
    def _run_native(exe_path: Path, clean_input: str, timeout_sec: float, cwd: Path) -> ExecutionResult:
        start_time = time.time()
        process: Optional[subprocess.Popen] = None

        # Only sharing/access violations are worth retrying (file still locked by a scanner).
        # Policy blocks are deterministic, so retrying them just wastes seconds per test.
        for attempt in range(4):
            try:
                process = subprocess.Popen(
                    [str(exe_path)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(cwd),
                    env=SandboxRunner._sanitized_env(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                break
            except OSError as e:
                winerror = getattr(e, "winerror", None)
                if winerror in (5, 32) and attempt < 3:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                return SandboxRunner._launch_failure(e, time.time() - start_time)

        if process is None:
            return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=-1,
                                   stderr="Failed to spawn process.", error_type="SPAWN_FAILED")

        launched_at = time.time()
        try:
            stdout_b, stderr_b = process.communicate(input=clean_input.encode("utf-8"), timeout=timeout_sec)
        except subprocess.TimeoutExpired as e:
            SandboxRunner._kill_process_tree(process)
            try:
                stdout_b, stderr_b = process.communicate(timeout=2)
            except Exception:
                stdout_b, stderr_b = (e.stdout or b""), (e.stderr or b"")
            return SandboxRunner._timeout_result(stdout_b, stderr_b, timeout_sec, time.time() - launched_at)
        except Exception as e:
            SandboxRunner._kill_process_tree(process)
            return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=-1,
                                   stderr=f"Unexpected runner failure: {e}",
                                   duration_sec=time.time() - launched_at, error_type="RUNNER_EXCEPTION")

        return SandboxRunner._completed_result(process.returncode, stdout_b, stderr_b, time.time() - launched_at, wsl=False)

    @staticmethod
    def _launch_failure(e: OSError, duration: float) -> ExecutionResult:
        winerror = getattr(e, "winerror", None)
        if winerror == 4551:
            err_type = "BLOCKED_BY_SMART_APP_CONTROL"
            msg = ("Windows Smart App Control blocked this freshly compiled program (WinError 4551). "
                   "Use the WSL execution backend (Settings) or turn Smart App Control off.")
        elif winerror == 225:
            err_type = "BLOCKED_BY_ANTIVIRUS"
            msg = "Windows Defender flagged the compiled program as unsafe and blocked it (WinError 225)."
        else:
            err_type = "OS_LAUNCH_BLOCKED"
            msg = f"The operating system refused to start the program: {e}"
        return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=-1, stderr=msg,
                               duration_sec=duration, error_type=err_type)

    # ------------------------------------------------------------------ WSL

    @staticmethod
    def _run_in_wsl(exe_path: Path, clean_input: str, timeout_sec: float, cwd: Path) -> ExecutionResult:
        from grader.config import SystemConfig
        cmd = [
            "wsl.exe", "-e", "sh", "-c", _WSL_RUN_SCRIPT, "sh",
            SystemConfig.to_wsl_path(cwd), f"{timeout_sec:g}", SystemConfig.to_wsl_path(exe_path)
        ]
        start = time.time()
        try:
            process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        except OSError as e:
            return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=-1,
                                   stderr=f"Could not start WSL: {e}", error_type="WSL_UNAVAILABLE")

        try:
            # `timeout` inside WSL enforces the real limit; the outer one is only a safety net
            stdout_b, stderr_b = process.communicate(input=clean_input.encode("utf-8"), timeout=timeout_sec + 15)
        except subprocess.TimeoutExpired:
            SandboxRunner._kill_process_tree(process)
            stdout_b, stderr_b = process.communicate()
            return SandboxRunner._timeout_result(stdout_b, stderr_b, timeout_sec, time.time() - start)

        duration = time.time() - start
        rc = process.returncode
        if rc == 124:
            return SandboxRunner._timeout_result(stdout_b, stderr_b, timeout_sec, duration)
        if rc == 125 and not stdout_b:
            return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=rc,
                                   stderr=SandboxRunner._decode(stderr_b)[0] or "WSL sandbox failed to start the program.",
                                   duration_sec=duration, error_type="WSL_SANDBOX_ERROR")
        # Drop coreutils' own "the monitored command dumped core" chatter
        stderr_text = b"\n".join(l for l in stderr_b.splitlines() if not l.startswith(b"timeout: "))
        return SandboxRunner._completed_result(rc, stdout_b, stderr_text, duration, wsl=True)

    # ------------------------------------------------------------------ shared

    @staticmethod
    def _decode(data: bytes) -> tuple:
        text = (data or b"").decode("utf-8", errors="replace")
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n...[output truncated]", True
        return text, False

    @staticmethod
    def _timeout_result(stdout_b: bytes, stderr_b: bytes, timeout_sec: float, duration: float) -> ExecutionResult:
        stdout, truncated = SandboxRunner._decode(stdout_b)
        stderr, _ = SandboxRunner._decode(stderr_b)
        return ExecutionResult(
            status=CheckStatus.TIMEOUT,
            returncode=-1,
            stdout=stdout,
            stderr=stderr or f"Execution exceeded timeout limit ({timeout_sec:.1f}s). Program may have an infinite loop or be waiting for more input than the test provides.",
            duration_sec=duration,
            timed_out=True,
            error_type="TIMEOUT",
            output_truncated=truncated
        )

    @staticmethod
    def _completed_result(ret_code: int, stdout_b: bytes, stderr_b: bytes, duration: float, wsl: bool) -> ExecutionResult:
        stdout, truncated = SandboxRunner._decode(stdout_b)
        stderr, _ = SandboxRunner._decode(stderr_b)
        if ret_code == 0:
            return ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout=stdout, stderr=stderr,
                                   duration_sec=duration, output_truncated=truncated)

        crash_type = SandboxRunner._classify_crash_code(ret_code, wsl=wsl)
        if crash_type is None:
            # A plain non-zero exit (e.g. `return 1;`, `exit(2)`, or garbage from `void main()`) is not a
            # crash: the output still gets compared, and the exit code is reported alongside it.
            return ExecutionResult(status=CheckStatus.PASS, returncode=ret_code, stdout=stdout, stderr=stderr,
                                   duration_sec=duration, error_type=f"NON_ZERO_EXIT_CODE ({ret_code})",
                                   output_truncated=truncated)
        return ExecutionResult(status=CheckStatus.RUNTIME_ERROR, returncode=ret_code, stdout=stdout, stderr=stderr,
                               duration_sec=duration, error_type=crash_type, output_truncated=truncated)

    @staticmethod
    def _kill_process_tree(process: Optional[subprocess.Popen]) -> None:
        """Kills process and all its children to prevent lingering zombie processes."""
        if process is None:
            return

        try:
            if sys.platform == "win32":
                # Windows taskkill forcefully terminates process tree
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                process.kill()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    @staticmethod
    def _classify_crash_code(code: int, wsl: bool = False) -> Optional[str]:
        """Translates an exit code into a crash classification, or None for an ordinary non-zero exit."""
        if wsl or sys.platform != "win32":
            # 128+N from a shell / coreutils timeout, or -N from Python for a signal
            unix_signals: Dict[int, str] = {
                11: "SEGMENTATION_FAULT (SIGSEGV)",
                6: "ABORT (SIGABRT)",
                8: "FLOATING_POINT_EXCEPTION (SIGFPE, e.g. division by zero)",
                7: "BUS_ERROR (SIGBUS)",
                9: "KILLED (SIGKILL, likely out of memory)",
                4: "ILLEGAL_INSTRUCTION (SIGILL)",
            }
            for sig, label in unix_signals.items():
                if code in (-sig, 128 + sig):
                    return label
            if code in (-25, 153):
                return "OUTPUT_LIMIT_EXCEEDED (SIGXFSZ)"
            return None

        # Windows NTSTATUS codes (convert to unsigned 32-bit)
        unsigned_code = code & 0xFFFFFFFF
        windows_signals: Dict[int, str] = {
            0xC0000005: "SEGMENTATION_FAULT (Access Violation 0xC0000005)",
            0xC0000094: "DIVISION_BY_ZERO (Integer Divide By Zero 0xC0000094)",
            0xC000008E: "DIVISION_BY_ZERO (Float Divide By Zero 0xC000008E)",
            0xC00000FD: "STACK_OVERFLOW (Stack Overflow 0xC00000FD)",
            0xC0000409: "STACK_BUFFER_OVERRUN (0xC0000409)",
            0xC0000374: "HEAP_CORRUPTION (0xC0000374)",
            0xC000001D: "ILLEGAL_INSTRUCTION (0xC000001D)",
            0xC0000135: "MISSING_DLL (DLL Not Found 0xC0000135)",
            0xC0000142: "DLL_INIT_FAILED (0xC0000142)",
            0xC0000028: "BAD_STACK (0xC0000028)",
            0x40010005: "CTRL_C_EXIT (0x40010005)",
        }
        if unsigned_code in windows_signals:
            return windows_signals[unsigned_code]
        if code == 3:
            return "ABORT (abort() / assertion failure)"
        if unsigned_code >= 0xC0000000:
            return f"CRASH (NTSTATUS 0x{unsigned_code:08X})"
        return None
