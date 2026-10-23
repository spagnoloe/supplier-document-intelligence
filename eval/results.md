# Eval results

## 1. Line matching

| Source type | Expected | Found | Missed | Extra |
|---|---|---|---|---|
| email | 3 | 3 | 0 | 0 |
| image | 12 | 12 | 0 | 0 |
| json | 11 | 11 | 0 | 0 |
| pdf | 11 | 11 | 0 | 0 |
| spreadsheet | 6 | 6 | 0 | 0 |
| all | 43 | 43 | 0 | 0 |

## 2. Field accuracy (matched lines)

| Source type | Line fields | Quotation terms |
|---|---|---|
| email | 30/30 (100%) | 5/5 (100%) |
| image | 112/132 (85%) | 9/10 (90%) |
| json | 114/114 (100%) | 15/15 (100%) |
| pdf | 115/116 (99%) | 10/10 (100%) |
| spreadsheet | 66/66 (100%) | 5/5 (100%) |
| all | 437/458 (95%) | 44/45 (98%) |

| Line field | email | image | json | pdf | spreadsheet | all |
|---|---|---|---|---|---|---|
| active_ingredients | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| brand_name | 3/3 (100%) | 11/12 (92%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 42/43 (98%) |
| discount_pct | 3/3 (100%) | 9/12 (75%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 40/43 (93%) |
| dosage_form | 3/3 (100%) | 11/12 (92%) | 11/11 (100%) | 10/11 (91%) | 6/6 (100%) | 41/43 (95%) |
| line_total | 3/3 (100%) | 6/12 (50%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 37/43 (86%) |
| pack_price | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| pack_size | 3/3 (100%) | 11/12 (92%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 42/43 (98%) |
| price_tiers | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| quantity | - | 6/12 (50%) | 4/4 (100%) | 6/6 (100%) | 6/6 (100%) | 22/28 (79%) |
| unit_price | 3/3 (100%) | 10/12 (83%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 41/43 (95%) |
| uom | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |

| Quotation term | email | image | json | pdf | spreadsheet | all |
|---|---|---|---|---|---|---|
| currency | 1/1 (100%) | 2/2 (100%) | 3/3 (100%) | 2/2 (100%) | 1/1 (100%) | 9/9 (100%) |
| incoterm | 1/1 (100%) | 2/2 (100%) | 3/3 (100%) | 2/2 (100%) | 1/1 (100%) | 9/9 (100%) |
| incoterm_place | 1/1 (100%) | 2/2 (100%) | 3/3 (100%) | 2/2 (100%) | 1/1 (100%) | 9/9 (100%) |
| quote_ref | 1/1 (100%) | 1/2 (50%) | 3/3 (100%) | 2/2 (100%) | 1/1 (100%) | 8/9 (89%) |
| supplier | 1/1 (100%) | 2/2 (100%) | 3/3 (100%) | 2/2 (100%) | 1/1 (100%) | 9/9 (100%) |

## 3. Flagging quality

| Source type | Wrong fields flagged |
|---|---|
| email | - |
| image | 6/21 (29%) |
| json | - |
| pdf | 0/1 (0%) |
| spreadsheet | - |
| all | 6/22 (27%) |

## 4. Review load

| Source type | Lines needs_review | Fields flagged | Flags on correct fields |
|---|---|---|---|
| email | 1/3 (33%) | 1/24 (4%) | 1/18 (6%) |
| image | 12/12 (100%) | 62/115 (54%) | 32/75 (43%) |
| json | 0/11 (0%) | 0/84 (0%) | 0/70 (0%) |
| pdf | 5/11 (45%) | 8/105 (8%) | 6/86 (7%) |
| spreadsheet | 3/6 (50%) | 3/60 (5%) | 1/52 (2%) |
| all | 21/43 (49%) | 74/388 (19%) | 40/301 (13%) |

## Wrong fields

| Source | Line | Field | Expected | Extracted | Flagged |
|---|---|---|---|---|---|
| mekong_pharma_quotation_MKP-2026-0812.pdf | 3 | dosage_form | {"any_of": ["Pressurised inhalation, suspension", "Pressurised inhalation suspension", "Inhalation aerosol"]} | "Pressurised inhalation canister suspension" | **no** |
| scan_02_lowres_fax_andina_p1.png | 1 | unit_price | 0.0091 | 0.0094 | yes |
| scan_02_lowres_fax_andina_p1.png | 4 | dosage_form | "Powder for oral suspension" | "Oral suspension" | **no** |
| scan_03_glare_partial_andina_p1.jpg | - | quote_ref | "FA-COT-2026-118" | "FA-COT-2026-158" | **no** |
| scan_03_glare_partial_andina_p1.jpg | 1 | quantity | null | 52462.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 1 | line_total | 52962.0 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 3 | pack_size | 30 | null | yes |
| scan_03_glare_partial_andina_p1.jpg | 3 | quantity | null | 46.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 3 | line_total | 46740.0 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | brand_name | "Amlodin 5" | "Ambodin 5" | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | discount_pct | 3.0 | 5.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | quantity | null | 22.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | line_total | 22348.8 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 4 | discount_pct | 0.0 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 4 | quantity | 120000 | 120.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 4 | line_total | 41400.0 | 41.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 5 | quantity | 3000000 | 3000.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 5 | line_total | 21090.0 | 21.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 6 | unit_price | 0.412 | 0.432 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | discount_pct | 0.0 | 10.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | quantity | 90000 | 90.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | line_total | 37080.0 | 37.0 | yes |
