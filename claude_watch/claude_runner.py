import asyncio
import os
import shlex


def _cmd_parts() -> list[str]:
    cmd = [os.environ.get("CLAUDE_CMD", "claude")]
    extra = os.environ.get("CLAUDE_EXTRA_ARGS", "")
    if extra:
        cmd.extend(shlex.split(extra))
    return cmd


def _build_argv(prompt: str, mode: str, session_id: str | None) -> list[str]:
    """Build the `claude` argv for the given mode.

    - "new": `[*cmd, "-p", prompt]` (Phase 1 behaviour, unchanged)
    - "continue": `[*cmd, "-p", "-c", prompt]`
    - "resume": `[*cmd, "-p", "-r", session_id, prompt]` (session_id required)

    Raises ValueError for mode="resume" without session_id, or unknown modes.
    """
    base = [*_cmd_parts(), "-p"]
    if mode == "new":
        return [*base, prompt]
    if mode == "continue":
        return [*base, "-c", prompt]
    if mode == "resume":
        if not session_id:
            raise ValueError("mode='resume' requires session_id")
        return [*base, "-r", session_id, prompt]
    raise ValueError(f"unknown mode: {mode}")


async def run_claude(
    prompt: str,
    timeout: int | None = None,
    *,
    mode: str = "new",
    session_id: str | None = None,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Run `claude -p [...] <prompt>` and return (returncode, stdout, stderr).

    `mode` selects how the session is chosen:
    - "new" (default): fresh session each call (Phase 1 behaviour)
    - "continue": `-c` (continue the most recent session in `cwd`)
    - "resume": `-r <session_id>` (resume a specific session)

    Returns rc=124 with stderr="timeout after Ns" on timeout. The subprocess is
    killed and reaped before returning. Returns rc=126 if the subprocess itself
    fails to start (e.g. invalid `cwd`).
    """
    # Validate inputs (mode/session_id) before the debug short-circuit so that
    # debug mode and real execution share the same input-validation contract.
    argv = _build_argv(prompt, mode, session_id)

    if os.environ.get("CLAUDE_WATCH_DEBUG") == "1":
        return (0, f"[debug] mode={mode} cwd={cwd} echo: {prompt}", "")

    timeout = timeout or int(os.environ.get("CLAUDE_TIMEOUT_SEC", "120"))

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return (126, "", f"failed to start claude: {e}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return (124, "", f"timeout after {timeout}s")
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
