"""The owner's Meta access token, kept out of the repository and out of every response.

Resolution: the environment variable named in the config, else the owner-only (0600) token file. The token is set with `python -m app.meta_publishing.token set`
(typed at a hidden prompt; it never appears in a command line, a log or a shell history). Nothing here returns a token to a caller other than the Graph client."""

from __future__ import annotations

import getpass
import os
import re
import sys
from pathlib import Path

from app.core.env import env_value
from app.meta_publishing.config import InstagramSettings, meta_config

_SHAPE = re.compile(r"^[A-Za-z0-9_\-|.]{20,}$")


class TokenError(RuntimeError):
    pass


class TokenStore:
    def __init__(self, settings: InstagramSettings | None = None):
        self.settings = settings or meta_config().instagram

    @property
    def path(self) -> Path:
        return Path(self.settings.token_file).expanduser()

    def source(self) -> str | None:
        """Where the token comes from ("environment" or "file"), None when none is set. Never the token."""
        if env_value(self.settings.token_env):
            return "environment"
        return "file" if self.path.is_file() and self.path.read_text(encoding="utf-8").strip() else None

    def get(self) -> str:
        token = env_value(self.settings.token_env)
        if not token and self.path.is_file():
            token = self.path.read_text(encoding="utf-8").strip()
        if not token:
            raise TokenError("No Meta access token is configured. Run: python -m app.meta_publishing.token set")
        return token.strip()

    def set(self, token: str) -> None:
        token = token.strip()
        if not _SHAPE.match(token):
            raise TokenError("That does not look like an access token (letters, digits and _-|. only, at least 20 characters).")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            f.write(token)
        os.chmod(self.path, 0o600)

    def clear(self) -> bool:
        if self.path.is_file():
            self.path.unlink()
            return True
        return False

    def redact(self, text: str) -> str:
        """The text with the token (if one is set) replaced, so that an error from Meta can never carry it into a log or a response."""
        try:
            token = self.get()
        except TokenError:
            return text
        return text.replace(token, "[token]")


def main(argv: list[str]) -> int:
    store, command = TokenStore(), (argv[0] if argv else "status")
    if command == "set":
        try:
            store.set(getpass.getpass("Meta access token (hidden): "))
        except TokenError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Token saved to {store.path} (owner-only). Restart is not needed.")
    elif command == "clear":
        print("Token file removed." if store.clear() else "No token file.")
    elif command == "status":
        print(f"token: {store.source() or 'not configured'}")
    else:
        print("usage: python -m app.meta_publishing.token [set|status|clear]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
