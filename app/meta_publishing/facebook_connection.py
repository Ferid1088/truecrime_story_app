"""The connection of the Farsi Facebook Page: status and a read-only Test connection. There is no Connect or Disconnect here: the Page is reached with the same Meta system-user token as
Instagram (one credential in the owner-only token file, never copied), so removing it would also disconnect Instagram; it is replaced where it lives.

Test connection (reads only): the token's permissions include every permission a Page Reel needs; the Page is the configured one (id) and is linked to the configured Instagram account; the
token lists the Page with the task that creates content; Meta gives a Page access token that identifies as the Page; and Meta's own upload limits are read. The outcome is remembered for the
Monitoring page. No token is ever returned."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.db.models import PublicationAccount
from app.meta_publishing.config import FacebookSettings, InstagramSettings, meta_config
from app.meta_publishing.connection import ConnectError
from app.meta_publishing.facebook_client import FacebookClient
from app.meta_publishing.graph import MetaError
from app.meta_publishing.token import TokenError, TokenStore
from app.publishing.registry import PublicationRegistry
from app.utils import utc_now

PLATFORM = "facebook"
SECRET_REF = "meta/system_user_token"


class FacebookConnection:
    def __init__(self, settings: InstagramSettings | None = None, facebook: FacebookSettings | None = None, *, language: str | None = None, tokens: TokenStore | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, now=utc_now):
        """The connection of one language's Page (`language`; the first configured Page when not given). A language without a Page raises ValueError."""
        config = meta_config()
        facebook = facebook or config.facebook
        self.s, self.f = settings or config.instagram, facebook.for_language(language) if language else facebook
        self.tokens = tokens or TokenStore(self.s)
        self.transport, self.now = transport, now

    @property
    def state_path(self) -> Path:
        return Path(self.f.state_file.format(language=self.f.language)).expanduser()

    def _facts(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_facts(self, facts: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(facts, f)
        os.replace(temporary, self.state_path)

    def client(self) -> FacebookClient:
        return FacebookClient(self.s, self.f, self.tokens, transport=self.transport)

    async def test(self) -> dict:
        """Read-only. Raises ConnectError (the reason is a short code); the outcome, good or bad, is remembered."""
        if not self.s.connection.live_enabled:
            raise ConnectError("live_disabled", "Live connection calls are off (config/meta_publishing.json: instagram.connection.live_enabled is false).")
        try:
            self.tokens.get()
        except TokenError as exc:
            raise ConnectError("not_connected", "No Meta access token is configured: Facebook uses the same system-user token as Instagram.") from exc
        facts = self._facts()
        client = self.client()
        try:
            result = await self._check(client)
        except ConnectError as exc:
            self._remember_failure(facts, exc)
            raise
        except MetaError as exc:
            error = ConnectError("token_expired" if exc.kind == "token_expired" else "token_invalid" if exc.kind == "token_invalid" else "meta_error", f"Meta answered: {exc}")
            self._remember_failure(facts, error)
            raise error from exc
        facts.update({"page_id": result["page"]["id"], "page_name": result["page"]["name"], "tasks": result["tasks"], "granted": sorted({*result["permissions"], *result["cover_permissions"]}), "upload_limits": result["upload_limits"],
                      "last_test": {"ok": True, "at": result["checked_at"], "error": None}})
        self._write_facts(facts)
        return result

    async def _check(self, client: FacebookClient) -> dict:
        granted = await client.granted_permissions()
        missing = [p for p in self.f.required_permissions if p not in granted]
        if missing:
            raise ConnectError("permission_missing", f"The token does not hold: {', '.join(missing)}. Generate the system-user token again with these permissions.")
        page = await client.page()
        expected = self.f.page
        if str(page.get("id")) != expected.page_id:
            raise ConnectError("page_mismatch", f"The token reaches Facebook Page {page.get('id')}, not the configured Page {expected.page_id}. Nothing was changed.")
        linked = page.get("instagram_business_account") or {}
        if str(linked.get("id")) != expected.linked_instagram_id:
            raise ConnectError("instagram_link_mismatch", f"The Page is linked to Instagram account {linked.get('id') or 'none'}, not {expected.linked_instagram_id}.")
        managed = await client.managed_page()
        if managed is None:
            raise ConnectError("page_not_managed", "The token does not list this Page: assign the Page to the system user in Business Settings.")
        if self.f.required_task not in (managed.get("tasks") or []):
            raise ConnectError("task_missing", f"The token may not do {self.f.required_task} on this Page. Give the system user full control of the Page.")
        identity = await client.page_token_identity()
        if str(identity.get("id")) != expected.page_id:
            raise ConnectError("page_token_mismatch", "The Page access token does not identify as the configured Page.")
        limits = await client.upload_limits()
        return {"page": {"id": expected.page_id, "name": page.get("name") or expected.brand}, "linked_instagram": {"id": linked["id"], "username": linked.get("username")},
                "permissions": sorted(p for p in granted if p in self.f.required_permissions),
                "cover_permissions": sorted(p for p in granted if p in self.f.cover.required_permissions), "tasks": sorted(managed.get("tasks") or []), "page_token": "obtained and verified",
                "upload_limits": limits, "checked_at": self.now().isoformat()}

    def _remember_failure(self, facts: dict, error: ConnectError) -> None:
        facts["last_test"] = {"ok": False, "at": self.now().isoformat(), "error": f"{error.reason}: {self.tokens.redact(str(error))}"[:400]}
        self._write_facts(facts)

    def status(self) -> dict:
        """What the interface may show (the stored facts and the configuration: no call to Meta). Never a token."""
        facts, source = self._facts(), self.tokens.source()
        last = facts.get("last_test")
        verified = bool(last and last["ok"] and facts.get("page_id") == self.f.page.page_id)
        blockers = []
        if source is None:
            blockers.append("No Meta access token is configured.")
        elif last is None:
            blockers.append("The connection was never tested: press Test connection.")
        elif not last["ok"]:
            blockers.append(f"The last connection test failed: {last['error']}")
        if not self.f.live_enabled:
            blockers.append("Uploads are switched off (config/meta_publishing.json: facebook.live_enabled is false).")
        missing_for_cover = [p for p in self.f.cover.required_permissions if p not in facts.get("granted", [])]
        return {"language": self.f.language, "brand": self.f.page.brand, "page": {"id": self.f.page.page_id, "name": facts.get("page_name") or self.f.page.brand},
                "linked_instagram_id": self.f.page.linked_instagram_id, "token_source": source, "token_shared_with": "Instagram", "connected": source is not None, "verified": verified,
                "last_test": last, "live_enabled": self.f.live_enabled, "connection_live_enabled": self.s.connection.live_enabled, "allow_public_publish": self.f.allow_public_publish,
                "required_permissions": self.f.required_permissions, "granted_permissions": facts.get("granted", []), "tasks": facts.get("tasks", []),
                "upload_limits": facts.get("upload_limits"), "upload_ready": not blockers, "upload_blockers": blockers, "cover_ready": not missing_for_cover, "cover_missing_permissions": missing_for_cover,
                "checked_at": last["at"] if last else None, "days_since_test": round((self.now() - datetime.fromisoformat(last["at"]).replace(tzinfo=UTC)).total_seconds() / 86400, 1) if last else None}

    def sync(self, db: Session) -> PublicationAccount:
        """The registry's account row of the Page (the one that exists for the language: never a second one) mirrors the real connection."""
        account = PublicationRegistry(db).ensure_accounts(self.f.language)[PLATFORM]
        status = self.status()
        good = status["connected"] and status["verified"]
        wanted = ("connected" if good else "not_connected", self.f.page.page_id, f"Page: {status['page']['name']}", SECRET_REF if status["connected"] else "")
        if (account.status, account.external_id, account.display_name, account.secret_ref) != wanted:
            account.status, account.external_id, account.display_name, account.secret_ref = wanted
            db.commit()
        return account
