"""SSH-based AOS-CX switch client — for the "clear registration" rollback
flow (undoing an accidental bulk adoption).

Confirmed via live testing (see CLAUDE.md and ../code-demo's investigation):
AOS-CX's REST /cli troubleshooting endpoint only permits a narrow allowlist
of read-only "show" commands and rejects any config-changing command
outright — confirmed live that it 403s on "mist registration-code ...".
There is no confirmed REST config-object equivalent for this action either
(unlike the registration_code push, which has a confirmed PUT
/system/mist). So clearing a switch's Mist registration state —
`clear mist registration-info` — genuinely requires a real SSH session to
the switch's CLI, not a REST call, which is what this module does.

Per the official HPE AOS-CX Fundamentals Guide's own wording
("registration information is stored persistently... to remove the
registration info, use clear mist registration-info or zeroize"), this
only clears the switch's LOCAL registration state — it does not remove the
corresponding inventory entry from the Mist org itself. That's intentional:
per instruction, cleaning up the Mist-side entry is out of scope here (it's
a quick GUI action in the Mist portal) — this module only handles the
switch side, so an accidentally-adopted batch of switches stops being
manageable by Mist immediately, without needing to touch the Mist org at
all first.

Uses paramiko's interactive shell channel (invoke_shell()) rather than
exec_command(): AOS-CX's SSH management drops a session straight into its
own CLI as the login shell (like most network-vendor CLIs), and such
shells commonly don't support the SSH "exec" channel request type for
arbitrary command passthrough — only an interactive PTY ("shell") channel,
the same way a human would type at the terminal. Command output has no
clean end-of-response marker over a raw shell, so reading it is a
best-effort "wait for a quiet period" read (see `_drain()`), not a
guaranteed-complete capture.
"""
from __future__ import annotations

import logging
import socket
import time

import paramiko

logger = logging.getLogger("adopt.ssh_client")

_CLEAR_COMMAND = "clear mist registration-info"

# Phrases AOS-CX (or similar network CLIs) commonly use when asking for
# confirmation before a destructive action. If any of these show up in the
# command's own output, a "y" is sent automatically to proceed — this is a
# best-effort heuristic (not a confirmed AOS-CX behavior for this specific
# command), and the raw output/confirmation exchange is always returned in
# full so the operator can see exactly what happened either way.
_CONFIRM_TOKENS = ("y/n", "(y/n)", "yes/no", "continue?")


class SshClientError(Exception):
    """Raised for any SSH failure (connect, auth, or command execution)."""


def clear_mist_registration(
    ip: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: float = 15.0,
) -> str:
    """SSH into the switch at `ip` and run `clear mist registration-info`,
    auto-confirming a y/n-style prompt if the switch shows one. Returns the
    raw CLI output text (login banner/prompt excluded).

    Raises SshClientError on any connection/auth/command failure. Does not
    attempt to parse the CLI output for success/failure beyond that — the
    raw text is the source of truth here, same philosophy as this app's
    other raw-output surfaces (see ../code-demo's /cli-based functions).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except paramiko.AuthenticationException as exc:
        raise SshClientError(f"SSH authentication to {ip} failed: {exc}") from exc
    except (paramiko.SSHException, socket.error, OSError) as exc:
        raise SshClientError(f"Could not SSH to {ip}: {exc}") from exc

    try:
        shell = client.invoke_shell()
        shell.settimeout(timeout)
        _drain(shell, timeout=2.0)  # discard the initial login banner/prompt

        shell.send(_CLEAR_COMMAND + "\n")
        output = _drain(shell, timeout=timeout)

        if any(token in output.lower() for token in _CONFIRM_TOKENS):
            shell.send("y\n")
            output += _drain(shell, timeout=timeout)

        return output
    except (paramiko.SSHException, socket.error, OSError) as exc:
        raise SshClientError(f"Running '{_CLEAR_COMMAND}' on {ip} failed: {exc}") from exc
    finally:
        client.close()


def _drain(shell: paramiko.Channel, timeout: float) -> str:
    """Read from `shell` until no more data has arrived for a short quiet
    period, or `timeout` total elapses. Interactive-shell CLI output has no
    clean end-of-response marker, so this is a best-effort read."""
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    last_data_time = time.monotonic()
    while time.monotonic() < deadline:
        if shell.recv_ready():
            chunk = shell.recv(4096).decode("utf-8", errors="replace")
            chunks.append(chunk)
            last_data_time = time.monotonic()
        elif chunks and (time.monotonic() - last_data_time > 1.0):
            break
        else:
            time.sleep(0.1)
    return "".join(chunks)
