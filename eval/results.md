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
| image | 118/132 (89%) | 9/10 (90%) |
| json | 114/114 (100%) | 15/15 (100%) |
| pdf | 114/116 (98%) | 10/10 (100%) |
| spreadsheet | 66/66 (100%) | 5/5 (100%) |
| all | 442/458 (97%) | 44/45 (98%) |

| Line field | email | image | json | pdf | spreadsheet | all |
|---|---|---|---|---|---|---|
| active_ingredients | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 10/11 (91%) | 6/6 (100%) | 42/43 (98%) |
| brand_name | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| discount_pct | 3/3 (100%) | 7/12 (58%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 38/43 (88%) |
| dosage_form | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 10/11 (91%) | 6/6 (100%) | 42/43 (98%) |
| line_total | 3/3 (100%) | 6/12 (50%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 37/43 (86%) |
| pack_price | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| pack_size | 3/3 (100%) | 11/12 (92%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 42/43 (98%) |
| price_tiers | 3/3 (100%) | 12/12 (100%) | 11/11 (100%) | 11/11 (100%) | 6/6 (100%) | 43/43 (100%) |
| quantity | - | 12/12 (100%) | 4/4 (100%) | 6/6 (100%) | 6/6 (100%) | 28/28 (100%) |
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
| image | 9/15 (60%) |
| json | - |
| pdf | 1/2 (50%) |
| spreadsheet | - |
| all | 10/17 (59%) |

## 4. Review load

| Source type | Lines needs_review | Fields flagged | Flags on correct fields |
|---|---|---|---|
| email | 2/3 (67%) | 1/24 (4%) | 1/18 (6%) |
| image | 9/12 (75%) | 44/115 (38%) | 23/79 (29%) |
| json | 0/11 (0%) | 0/88 (0%) | 0/70 (0%) |
| pdf | 6/11 (55%) | 9/105 (9%) | 6/85 (7%) |
| spreadsheet | 3/6 (50%) | 3/60 (5%) | 1/52 (2%) |
| all | 20/43 (47%) | 57/392 (15%) | 31/304 (10%) |

## Wrong fields

| Source | Line | Field | Expected | Extracted | Flagged |
|---|---|---|---|---|---|
| mekong_pharma_quotation_MKP-2026-0812.pdf | 5 | active_ingredients | [{"inn": "ferrous sulfate", "strength_value": 60, "strength_unit": "mg"}, {"inn": "folic acid", "strength_value": 0.4, "strength_unit": "mg"}] | [{"name_as_written": "Ferrous sulfate", "inn": "ferrous sulfate", "strength_value": 60.0, "strength_unit": "mg (elemental Fe)"}, {"name_as_written": "Folic acid", "inn": "folic acid", "strength_value": 0.4, "strength_unit": "mg"}] | yes |
| mekong_pharma_quotation_MKP-2026-0812.pdf | 3 | dosage_form | {"any_of": ["Pressurised inhalation, suspension", "Pressurised inhalation suspension", "Inhalation aerosol"]} | "Pressurised inhalation canister suspension" | **no** |
| scan_02_lowres_fax_andina_p1.png | 1 | unit_price | 0.0091 | 0.0094 | yes |
| scan_03_glare_partial_andina_p1.jpg | - | quote_ref | "FA-COT-2026-118" | "FA-COT-2636-108" | **no** |
| scan_03_glare_partial_andina_p1.jpg | 1 | discount_pct | {"any_of": [null, 3.0]} | 5.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 1 | line_total | 52962.0 | 52462.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | discount_pct | 3.0 | 5.0 | **no** |
| scan_03_glare_partial_andina_p1.jpg | 2 | line_total | 22348.8 | 22.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 3 | pack_size | 30 | null | yes |
| scan_03_glare_partial_andina_p1.jpg | 3 | discount_pct | 5.0 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 3 | line_total | 46740.0 | 46.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 4 | discount_pct | 0.0 | null | **no** |
| scan_03_glare_partial_andina_p1.jpg | 4 | line_total | 41400.0 | 41.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 5 | line_total | 21090.0 | 21.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | unit_price | 0.412 | 0.432 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | discount_pct | 0.0 | 5.0 | yes |
| scan_03_glare_partial_andina_p1.jpg | 6 | line_total | 37080.0 | 37.0 | yes |
