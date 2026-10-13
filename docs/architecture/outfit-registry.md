# Outfit registry

The live thumbnail host loader is `app/thumbnails/hosts.py:46-63`: it reads
the approved-asset manifest configured by `app/core/ai_config.py:1788-1789`
(`data/host/manifest.json`) and resolves each manifest file relative to that
manifest. The new generated-pose registry is `app/outfits/registry.py:26-75`;
it reads `data/outfits/<slug>`, validates PNG files and required poses, and
returns `Outfit(outfit_id, label, poses)` records. The validation test is
`tests/test_outfit_registry.py:7-26`; no image files were changed.

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
