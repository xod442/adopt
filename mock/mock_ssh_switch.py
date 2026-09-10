"""Mock AOS-CX switch SSH server, for exercising app/ssh_client.py end-to-end
without real hardware.

Simulates just enough of AOS-CX's SSH CLI behavior for testing:
  - password auth (configurable valid username/password)
  - an interactive shell channel (not exec_command — see app/ssh_client.py's
    module docstring for why) that, upon receiving
    "clear mist registration-info", responds with a canned success message

Run standalone: `python mock_ssh_switch.py` (reads MOCK_SSH_PORT, default
9101, MOCK_SSH_USER/MOCK_SSH_PASS, default admin/admin).
"""
from __future__ import annotations

import os
import socket
import threading

import paramiko

_HOST_KEY = paramiko.RSAKey.generate(2048)


class _ServerInterface(paramiko.ServerInterface):
    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self.shell_requested = threading.Event()

    def check_auth_password(self, username, password):
        if username == self._username and password == self._password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel):
        self.shell_requested.set()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True


class MockSshSwitch:
    """A background thread accepting SSH connections on 127.0.0.1:<port>,
    one at a time (fine for tests). `port=0` picks a free port; read it
    back via `.port` after construction."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, username: str = "admin", password: str = "admin", require_confirm: bool = False):
        self.username = username
        self.password = password
        self.require_confirm = require_confirm
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._stopping = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=5)

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stopping:
            try:
                client_sock, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(client_sock,), daemon=True).start()

    def _serve(self, client_sock: socket.socket) -> None:
        transport = paramiko.Transport(client_sock)
        transport.add_server_key(_HOST_KEY)
        server = _ServerInterface(self.username, self.password)
        try:
            transport.start_server(server=server)
        except paramiko.SSHException:
            return

        channel = transport.accept(10)
        if channel is None:
            transport.close()
            return

        server.shell_requested.wait(10)
        try:
            channel.send("mock-switch login banner\r\nswitch# ")
            buffer = ""
            awaiting_confirm = False
            while True:
                data = channel.recv(1024)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")
                if "\n" in buffer or "\r" in buffer:
                    line = buffer.strip()
                    buffer = ""
                    if awaiting_confirm:
                        awaiting_confirm = False
                        if line.lower() in ("y", "yes"):
                            channel.send("\r\nRegistration info cleared successfully.\r\nswitch# ")
                        else:
                            channel.send("\r\nAborted.\r\nswitch# ")
                    elif "clear mist registration-info" in line:
                        if self.require_confirm:
                            awaiting_confirm = True
                            channel.send("\r\nThis will clear Mist registration. Continue? (y/n) ")
                        else:
                            channel.send("\r\nRegistration info cleared successfully.\r\nswitch# ")
                    elif line:
                        channel.send(f"\r\n% Unknown command: {line}\r\nswitch# ")
        except (EOFError, OSError):
            pass
        finally:
            try:
                channel.close()
            except OSError:
                pass
            transport.close()


if __name__ == "__main__":
    import time

    server = MockSshSwitch(
        host="0.0.0.0",
        port=int(os.getenv("MOCK_SSH_PORT", "9101")),
        username=os.getenv("MOCK_SSH_USER", "admin"),
        password=os.getenv("MOCK_SSH_PASS", "admin"),
    )
    server.start()
    print(f"Mock SSH switch listening on 0.0.0.0:{server.port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
