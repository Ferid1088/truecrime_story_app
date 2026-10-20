# Migrations 0049–0051 ship ahead of their code

Migrations 0049–0051 were committed together with the Discover migrations
(0052, 0053) because Alembic's chain is linear and 0052 sits on top of 0051.
They belong to other work that follows in later commits:

| Migration | Table | Belongs to |
|---|---|---|
| `0049_board_plans` | `board_plans` | detective board |
| `0050_shared_reel_assets` | `shared_reel_assets` | shared reel assets (host-led shorts) |
| `0051_host_led_music_tracks` | `host_led_music_tracks`, `host_led_music_usage` | host-led music |

Until those commits land, the tables exist in the schema with no model in the
code. The migrations were already applied to the live database and must stay
byte-identical.
