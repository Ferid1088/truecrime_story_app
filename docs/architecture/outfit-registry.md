# Outfit registry

The sole live thumbnail source is `app/outfits/registry.py:34-112`; it reads
`data/outfits/<slug>` and validates PNG files and required poses. The host
adapter at `app/thumbnails/hosts.py:43-61` consumes those registry records and
does not read a manifest. The old `data/host/manifest.json` is retained as
user data and historical evidence only; no application loader reads it. The
validation and compatibility tests are `tests/test_outfit_registry.py:8-31`;
no image files were changed.

## Exact legacy manifest mapping

`data/host/manifest.json:6-537` contains 102 entries under `OUTFIT_01` through
`OUTFIT_17`; every entry has `avatar_look_ids: []`. The registry keeps those
ids as aliases, with this exact mapping:

| manifest id | registry outfit id | manifest name |
|---|---|---|
| `OUTFIT_01` | `rust_overshirt_chinos` | Rust Overshirt |
| `OUTFIT_02` | `navy_suit` | Navy Suit |
| `OUTFIT_03` | `navy_cord_blazer_vest` | Corduroy Three-Piece |
| `OUTFIT_04` | `grey_blazer_jeans` | Grey Blazer & Jeans |
| `OUTFIT_05` | `navy_blazer_beige_chinos` | Navy Blazer & Chinos |
| `OUTFIT_06` | `navy_quarterzip_tie` | Navy Quarter-Zip |
| `OUTFIT_07` | `white_tee_beige_sweater` | Beige Knit Shoulders |
| `OUTFIT_08` | `thobe_ghutra_sunglasses` | Thobe & Ghutra (Sunglasses) |
| `OUTFIT_09` | `thobe_ghutra` | Thobe & Ghutra |
| `OUTFIT_10` | `thobe_mandarin_sneakers` | White Thobe |
| `OUTFIT_11` | `lightblue_polo_cream_sweater` | Polo & Cream Knit |
| `OUTFIT_12` | `olive_sweater_beige_chinos` | Olive Sweater & Chinos |
| `OUTFIT_13` | `navy_polo_beige_pleated` | Navy Polo & Beige Trousers |
| `OUTFIT_14` | `navy_fieldjacket_turtleneck` | Turtleneck & Navy Jacket |
| `OUTFIT_15` | `black_shirt_dark_trousers` | Casual Navy |
| `OUTFIT_16` | `brown_sweater_navy_chinos` | Brown Crewneck Sweater |
| `OUTFIT_17` | `lightblue_shirt_white_tee` | Oxford Shirt & Chinos |

The repository contains the HeyGen provider at `app/providers/avatar/heygen.py`,
but no imported HeyGen look-id data. The only look-id field in the manifest is
empty for all 102 entries, so the registry's `heygen_look_ids` fields are
empty. Neither `navy_shirt` nor `rust_shirt` occurs in the manifest or import;
they are not silently invented as ids.

## Existing HeyGen-look overlap

The first seven existing manifest looks are OUTFIT_01 Rust Overshirt, OUTFIT_02
Navy Suit, OUTFIT_03 Corduroy Three-Piece, OUTFIT_04 Grey Blazer & Jeans,
OUTFIT_05 Navy Blazer & Chinos, OUTFIT_06 Navy Quarter-Zip, and OUTFIT_07
Beige Knit Shoulders.

| generated slug | existing look overlap |
|---|---|
| `rust_overshirt_chinos` | OUTFIT_01 Rust Overshirt; same overshirt, chinos added |
| `navy_suit` | OUTFIT_02 Navy Suit; exact semantic match |
| `navy_cord_blazer_vest` | OUTFIT_03 Corduroy Three-Piece; closest semantic match |
| `grey_blazer_jeans` | OUTFIT_04 Grey Blazer & Jeans; exact semantic match |
| `navy_blazer_beige_chinos` | OUTFIT_05 Navy Blazer & Chinos; exact semantic match |
| `navy_quarterzip_tie` | OUTFIT_06 Navy Quarter-Zip; tie added |
| `white_tee_beige_sweater`, `lightblue_polo_cream_sweater` | closest to OUTFIT_07 Beige Knit Shoulders; not exact |
| `black_shirt_dark_trousers` and remaining slugs | no exact match among the first seven |

There is no `navy_shirt` or `rust_shirt` slug in the repository. Those names
are not existing registry or manifest IDs.

## Read-only image report

`scripts/report_outfits.py:16-48` reports every pose's dimensions, byte size,
and a conservative alpha-edge glow heuristic: at least three semi-transparent
near-white pixels (`0 < alpha < 220`, RGB channels ≥ 220). The command is
read-only. Current result by outfit is below; `halo` is true if any pose meets
that heuristic.

| outfit | PNG dimensions | total PNG bytes | halo |
|---|---|---:|---|
| `black_shirt_dark_trousers` | 1024×1536 | 10,174,315 | no |
| `brown_sweater_navy_chinos` | 1024×1536 | 10,918,556 | no |
| `grey_blazer_jeans` | 1024×1536 | 12,396,554 | yes |
| `lightblue_polo_cream_sweater` | 1086×1448 | 6,000,748 | yes |
| `lightblue_shirt_white_tee` | 1024×1536 | 10,535,011 | yes |
| `navy_blazer_beige_chinos` | 1024×1536 | 10,228,533 | yes |
| `navy_cord_blazer_vest` | 1024×1536 | 12,159,240 | no |
| `navy_fieldjacket_turtleneck` | 1024×1536 | 11,153,166 | yes |
| `navy_polo_beige_pleated` | 1024×1536 | 9,975,134 | yes |
| `navy_quarterzip_tie` | 1024×1536 and 1122×1402 | 10,513,699 | yes |
| `navy_suit` | 1024×1536 | 9,974,858 | no |
| `olive_sweater_beige_chinos` | 1086×1448 | 6,169,131 | yes |
| `rust_overshirt_chinos` | 1024×1536 | 11,541,847 | yes |
| `thobe_ghutra` | 1024×1536 | 9,247,139 | yes |
| `thobe_ghutra_sunglasses` | 1086×1448 | 5,708,405 | yes |
| `thobe_mandarin_sneakers` | 1122×1402 | 5,025,391 | yes |
| `white_tee_beige_sweater` | 1024×1536 and 1122×1402 | 9,527,272 | yes |
