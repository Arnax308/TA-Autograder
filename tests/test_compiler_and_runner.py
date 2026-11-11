import pytest
from pathlib import Path
from grader.compiler import Compiler
from grader.runner import SandboxRunner
from grader.models import LanguageType, CheckStatus

def test_compiler_success(tmp_path):
    compiler = Compiler(log_dir=tmp_path / "logs")
    src = tmp_path / "main.c"
    src.write_text('#include <stdio.h>\nint main() { printf("Hello\\n"); return 0; }', encoding="utf-8")

    out_exe = tmp_path / "main.exe"
    res = compiler.compile([src], out_exe, language=LanguageType.C)

    assert res.success is True
    assert res.returncode == 0
    assert out_exe.exists()

def test_compiler_syntax_error(tmp_path):
    compiler = Compiler(log_dir=tmp_path / "logs")
    src = tmp_path / "bad.c"
    src.write_text('#include <stdio.h>\nint main() { printf("Missing semicolon") return 0; }', encoding="utf-8")

    out_exe = tmp_path / "bad.exe"
    res = compiler.compile([src], out_exe, language=LanguageType.C)

    assert res.success is False
    assert res.concise_error is not None
    assert "error" in res.concise_error.lower()

def test_runner_execution_and_timeout(tmp_path):
    compiler = Compiler(log_dir=tmp_path / "logs")
    src = tmp_path / "loop.c"
    src.write_text('#include <stdio.h>\nint main() { while(1) {} return 0; }', encoding="utf-8")

    out_exe = tmp_path / "loop.exe"
    compiler.compile([src], out_exe, language=LanguageType.C)

    res = SandboxRunner.run_executable(out_exe, timeout_sec=1.0)
    assert res.status == CheckStatus.TIMEOUT
    assert res.timed_out is True

def test_runner_segfault(tmp_path):
    compiler = Compiler(log_dir=tmp_path / "logs")
    src = tmp_path / "crash.c"
    src.write_text('#include <stdio.h>\nint main() { int *p = NULL; *p = 10; return 0; }', encoding="utf-8")

    out_exe = tmp_path / "crash.exe"
    compiler.compile([src], out_exe, language=LanguageType.C)

    res = SandboxRunner.run_executable(out_exe, timeout_sec=2.0)
    assert res.status == CheckStatus.RUNTIME_ERROR
