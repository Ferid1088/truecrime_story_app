"""The publication registry (2026-11-16): the Instagram table of the Meta module becomes the platform-independent `publications` table, with accounts, routing links and a
file-hash cache beside it. Films that were already published keep their YouTube link and date: each gets a publication row with the same URL and time.
Written with batch operations so that it runs on PostgreSQL (live) and on SQLite (the unit-test database)."""

import sqlalchemy as sa
from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None

_ONCE = "status IN ('published', 'publish_uncertain')"
_OLD_INDEXES = (("ix_meta_publish_operations_case_id", "ix_publications_case_id", ["case_id"]),
                ("ix_meta_publish_operations_content_hash", "ix_publications_content_hash", ["content_hash"]),
                ("ix_meta_publish_operations_status", "ix_publications_status", ["status"]))


def copy_published_films(connection) -> int:
    """One `youtube`/`film` publication (asset key `V<video id>`) per film that was marked published (or has a YouTube link or a publication date). Nothing in `videos`
    is changed; a film that already has such a row is left alone. The hash of a copied row is a marker (`legacy-video-<id>`): the file is not read during a migration."""
    rows = connection.execute(sa.text(
        "SELECT id, case_id, language, state, file_path, youtube_url, published_at, youtube_title FROM videos "
        "WHERE state = 'published' OR youtube_url IS NOT NULL OR published_at IS NOT NULL")).mappings().all()
    copied = 0
    for v in rows:
        if connection.execute(sa.text("SELECT 1 FROM publications WHERE video_id = :v AND platform = 'youtube' AND kind = 'film'"), {"v": v["id"]}).first():
            continue
        connection.execute(sa.text(
            "INSERT INTO publications (case_id, language, platform, kind, asset_key, video_id, account_id, content_hash, video_path, caption, visibility, mode, status, "
            "remote_url, published_at, attempt_count, hash_inputs_json, created_at, updated_at) VALUES "
            "(:case_id, :language, 'youtube', 'film', :asset_key, :video_id, '', :hash, :path, :caption, 'public', 'manual', :status, :url, :published_at, 0, '{}', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"case_id": v["case_id"], "language": v["language"], "asset_key": f"V{v['id']}", "video_id": v["id"], "hash": f"legacy-video-{v['id']}", "path": v["file_path"] or "",
             "caption": v["youtube_title"] or "", "status": "published" if v["state"] == "published" else "draft", "url": v["youtube_url"], "published_at": v["published_at"]})
        copied += 1
    return copied


def upgrade() -> None:
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    duplicates = connection.execute(sa.text(
        "SELECT 1 FROM meta_publish_operations GROUP BY case_id, language, clip_key HAVING count(*) > 1")).first()
    if duplicates:
        raise RuntimeError("meta_publish_operations holds several attempts for one clip; merge them before this migration")

    op.create_table(
        "publication_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("secret_ref", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_connected"),
        sa.Column("auto_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "language", name="uq_publication_account"),
    )

    op.drop_index("uq_meta_publish_once", table_name="meta_publish_operations")
    for old, _new, _columns in _OLD_INDEXES:
        op.drop_index(old, table_name="meta_publish_operations")
    op.rename_table("meta_publish_operations", "publications")
    if postgres:
        op.execute("ALTER TABLE publications RENAME CONSTRAINT meta_publish_operations_pkey TO publications_pkey")
        op.execute("ALTER SEQUENCE meta_publish_operations_id_seq RENAME TO publications_id_seq")

    with op.batch_alter_table("publications") as batch:
        batch.alter_column("clip_key", new_column_name="asset_key", existing_type=sa.String(10))
        batch.alter_column("media_id", new_column_name="remote_id", existing_type=sa.String(80), type_=sa.String(120))
        batch.alter_column("permalink", new_column_name="remote_url", existing_type=sa.Text())
        batch.alter_column("account_id", existing_type=sa.String(40), type_=sa.String(80), server_default="")
        batch.alter_column("platform", existing_type=sa.String(30), type_=sa.String(20), server_default=None)
        batch.alter_column("content_hash", existing_type=sa.String(64), server_default="")
        batch.alter_column("video_path", existing_type=sa.Text(), server_default="")
        batch.alter_column("status", existing_type=sa.String(20), server_default="draft")
        batch.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="short"))
        batch.add_column(sa.Column("video_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("short_clip_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("account_ref_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cover_ref", sa.String(80), nullable=True))
        batch.add_column(sa.Column("cover_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("visibility", sa.String(20), nullable=False, server_default="private"))
        batch.add_column(sa.Column("mode", sa.String(12), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("publication_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("hash_inputs_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("content_approved_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("content_approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("publication_approved_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("publication_approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_publications_video_id", "videos", ["video_id"], ["id"])
        batch.create_foreign_key("fk_publications_short_clip_id", "short_clips", ["short_clip_id"], ["id"])
        batch.create_foreign_key("fk_publications_account_ref_id", "publication_accounts", ["account_ref_id"], ["id"])
        batch.create_unique_constraint("uq_publication_item", ["case_id", "language", "platform", "kind", "asset_key"])
    op.execute("UPDATE publications SET platform = 'instagram' WHERE platform = 'instagram_reel'")
    for _old, new, columns in _OLD_INDEXES:
        op.create_index(new, "publications", columns)
    op.create_index("ix_publications_language", "publications", ["language"])
    op.create_index("ix_publications_video_id", "publications", ["video_id"])
    op.create_index("ix_publications_scheduled_at", "publications", ["scheduled_at"])
    op.create_index("uq_publication_once", "publications", ["platform", "account_id", "content_hash"], unique=True,
                    postgresql_where=sa.text(_ONCE), sqlite_where=sa.text(_ONCE))

    op.create_table(
        "routing_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("target_destination", sa.String(30), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("status", sa.String(26), nullable=False, server_default="manual_action_required"),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_by", sa.String(40), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("publication_id", "target_destination", "method", name="uq_routing_link"),
    )
    op.create_index("ix_routing_links_publication_id", "routing_links", ["publication_id"])

    op.create_table(
        "file_hash_cache",
        sa.Column("path", sa.String(1000), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    copy_published_films(connection)


def downgrade() -> None:
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    if connection.execute(sa.text("SELECT 1 FROM publications WHERE kind <> 'short' OR platform <> 'instagram' LIMIT 1")).first():
        raise RuntimeError("publications holds rows that the older Instagram-only table cannot keep; downgrade would lose them")
    op.drop_table("file_hash_cache")
    op.drop_index("ix_routing_links_publication_id", table_name="routing_links")
    op.drop_table("routing_links")
    op.drop_index("uq_publication_once", table_name="publications")
    for index in ("ix_publications_scheduled_at", "ix_publications_video_id", "ix_publications_language"):
        op.drop_index(index, table_name="publications")
    for _old, new, _columns in _OLD_INDEXES:
        op.drop_index(new, table_name="publications")
    op.execute("UPDATE publications SET platform = 'instagram_reel'")
    with op.batch_alter_table("publications") as batch:
        batch.drop_constraint("uq_publication_item", type_="unique")
        batch.drop_constraint("fk_publications_account_ref_id", type_="foreignkey")
        batch.drop_constraint("fk_publications_short_clip_id", type_="foreignkey")
        batch.drop_constraint("fk_publications_video_id", type_="foreignkey")
        for column in ("published_at", "attempt_count", "publication_approved_at", "publication_approved_hash", "content_approved_at", "content_approved_hash", "hash_inputs_json",
                       "publication_hash", "window_ends_at", "scheduled_at", "mode", "visibility", "cover_hash", "cover_ref", "account_ref_id", "short_clip_id", "video_id", "kind"):
            batch.drop_column(column)
        batch.alter_column("remote_url", new_column_name="permalink", existing_type=sa.Text())
        batch.alter_column("remote_id", new_column_name="media_id", existing_type=sa.String(120), type_=sa.String(80))
        batch.alter_column("asset_key", new_column_name="clip_key", existing_type=sa.String(10))
        batch.alter_column("platform", existing_type=sa.String(20), type_=sa.String(30), server_default="instagram_reel")
        batch.alter_column("account_id", existing_type=sa.String(80), type_=sa.String(40), server_default=None)
        batch.alter_column("status", existing_type=sa.String(20), server_default="created")
        batch.alter_column("content_hash", existing_type=sa.String(64), server_default=None)
        batch.alter_column("video_path", existing_type=sa.Text(), server_default=None)
    if postgres:
        op.execute("ALTER SEQUENCE publications_id_seq RENAME TO meta_publish_operations_id_seq")
        op.execute("ALTER TABLE publications RENAME CONSTRAINT publications_pkey TO meta_publish_operations_pkey")
    op.rename_table("publications", "meta_publish_operations")
    for old, _new, columns in _OLD_INDEXES:
        op.create_index(old, "meta_publish_operations", columns)
    op.create_index("uq_meta_publish_once", "meta_publish_operations", ["platform", "account_id", "content_hash"], unique=True,
                    postgresql_where=sa.text(_ONCE), sqlite_where=sa.text(_ONCE))
    op.drop_table("publication_accounts")
