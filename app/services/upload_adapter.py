"""Sandbox upload adapter used until channel credentials are connected."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UploadReceipt:
    upload_id: str
    visibility: str


class SandboxUploadAdapter:
    """Record a deterministic private/draft upload without network access."""

    def upload(self, *, bundle_id: int, bundle_hash: str, visibility: str) -> UploadReceipt:
        return UploadReceipt(
            upload_id=f"sandbox-bundle-{bundle_id}-{bundle_hash[:16]}",
            visibility=visibility,
        )
