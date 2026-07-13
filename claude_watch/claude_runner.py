import asyncio
import os
import shlex


def _cmd_parts() -> list[str]:
    cmd = [os.environ.get("CLAUDE_CMD", "claude")]
    extra = os.environ.get("CLAUDE_EXTRA_ARGS", "")
    if extra:
        cmd.extend(shlex.split(extra))
    return cmd


async def run_claude(prompt: str, timeout: int | None = None) -> tuple[int, str, str]:
    """Run `claude -p <prompt>` and return (returncode, stdout, stderr).

    Returns rc=124 with stderr="timeout after Ns" on timeout. The subprocess is
    killed and reaped before returning.
    """
    if os.environ.get("CLAUDE_WATCH_DEBUG") == "1":
        return (0, f"[debug] echo: {prompt}", "")

    timeout = timeout or int(os.environ.get("CLAUDE_TIMEOUT_SEC", "120"))
    argv = [*_cmd_parts(), "-p", prompt]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
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
