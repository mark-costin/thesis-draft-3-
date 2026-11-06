# Synthetic data: changes to the generator

This file records every change to the method of `ml/synthetic/01_syntgen.ipynb` after the
Synthetic Data System Design (Nov 3, 2026) and the physician's approval of its Section 9
settings (Nov 5, 2026, Dr. Asela C. Miranda, Part 5 of the signed form).

Every setting marked **author-defined (new, not yet physician-reviewed)** must be reviewed by the
physician before it is used for the thesis results. Every existing CFG/AD value is unchanged.

---

## Change 1: Phase 0, the label leak (Nov 2026)

### Problem

In Part E4 (`level_for`), a patient's values were set to the Moderate band in the 5 years before
their diagnosis:

```python
elif when.year - d.year <= 5:
    lvl = "mod"                     # not yet diagnosed, already drifting
```

The training label is "diagnosed within 5 years of the first visit" (`label_window_years: 5`), so
the values gave the label away. On the committed 5,000-patient data, a plain logistic regression on
the Low/Moderate/High bands alone reached AUROC 0.997 (CVD), 0.998 (T2D), 0.940 (CRD) and 0.982
(NEURO). No future case was in the Low band at the index visit, and 100% of future T2D and NEURO
cases were Moderate on every driven value.

Three yes/no inputs also used a diagnosis that could come **after** the index visit:
`gestational_diabetes` (from `gdm_if_t2d`), `acanthosis_nigricans` (from `acanthosis_if_t2d`) and
`allergic_rhinitis` (from `allergic_rhinitis_if_asthma`).

A second problem made the leak the only strong signal: a person's own diseases depended only on
age, sex (GBD rates) and the family multiplier. Smoking, exposures and lifestyle had no effect on
who got sick.

### New method

Values and diagnoses now share a **cause** (the person's underlying risk); neither is read off the
other.

1. **Risk factors are drawn first**, before the person's own diseases: smoking, exposures,
   lifestyle bands, gestational diabetes, allergic rhinitis, occupational dust, TBI.
2. **Personal multiplier per disease**:
   `M = exp(sum of log RR for each risk factor present + frailty_sd x N(0,1))`, divided by its
   population average so that the average multiplier is exactly 1. The GBD-calibrated rates and
   `prevalence_factor` therefore keep their meaning. The average is computed from the factor
   shares, not tuned.
3. **The person's own yearly onset chance** = `rate_fn(age, sex)` x family multiplier (unchanged)
   x M. Relatives are simulated exactly as before.
4. **Hypertension gets an onset year.** Before, it was present at every visit or never. The yearly
   chance of new hypertension is taken from the approved prevalence curve
   (`hypertension_chance`, `htn_scale`): the rise in prevalence from one year to the next. Without
   the multiplier this rebuilds the same curve exactly (tested).
5. **Values follow the risk.** At each visit, the person's 5-year risk of each disease
   (from age, sex, relatives diagnosed so far and M; never their own diagnosis) becomes
   `z` = SDs above the adult average. The average and SD come from the PSA age/sex weights with
   multiplier 1 and no family history; they never look at outcomes. Each value has a position:
   0-1 is the Low band, 1-2 Moderate, 2-3 High. It starts at the personal baseline (as before: `u`,
   and Moderate for `baseline_mod_share`) and moves up by
   `speed_person x speed_value x band_per_sd x max(0, z - drift_start_z)`.
   Before diagnosis, only the values that define a label stay below the rule (Change 3).
6. **After diagnosis values still go High** (unchanged, including the neuro episode rules and
   undiagnosed type 2 diabetes).
7. **Lifestyle values** (sodium, activity, carbohydrates, sedentary hours) are risk *inputs*: they
   keep the personal baseline before diagnosis and do not drift.
8. **The three yes/no flags** no longer look at a future diagnosis. Gestational diabetes and
   allergic rhinitis are drawn first and raise the risk of type 2 diabetes and asthma. Acanthosis
   is a marker of type 2 diabetes *risk* at the window start. (Change 3 adds a share for disease
   that began before the window.)

Overlap between future cases and non-cases is not set by any setting: onset stays random, so some
low-risk or slow-drifting people still get sick while their values look normal, and some
high-risk people never get sick but look borderline.

### Physician-approved shares kept as checks

`gdm_if_t2d`, `gdm_other`, `acanthosis_if_t2d`, `acanthosis_other`, `allergic_rhinitis_if_asthma`
and `allergic_rhinitis_other` were approved on Nov 5. They are no longer inputs. Notebook 2,
Cell 3b, reports the synthetic share next to each approved share (cases vs others). The relative
risks are **not** tuned to hit them. Since Change 3, acanthosis and allergic rhinitis are
*calibrated* to them (see there); gestational diabetes is report only.

### New settings

All are **author-defined (new, not yet physician-reviewed)**. They were fixed before the first run
and not changed to reach any AUROC.

| Setting | Value | Meaning |
|---|---|---|
| `P0["frailty_sd"]` | 0.5 | Spread of unexplained personal risk (log scale), one draw per disease |
| `P0["drift_start_z"]` | 0.0 | Drift starts once the 5-year risk is above the adult average |
| `P0["band_per_sd"]` | 0.5 | At average speed, each SD of risk above average moves a value half a band |
| `P0["speed_person_sd"]` | 0.5 | How much drift speed differs between people (log-normal) |
| `P0["speed_value_sd"]` | 0.25 | How much one person's values differ from each other (log-normal) |
| `P0["lifestyle_values"]` | sodium_mg, activity_min_week, carbs_g, sedentary_hours | Values treated as risk inputs (no drift) |
| `P0["gdm_share_female"]` | 0.04 | History of gestational diabetes in women (overall share of the previous run, 3.98%) |
| `P0["allergic_rhinitis_share"]` | 0.19, **0.175 since Change 3** | Allergic rhinitis before any disease |
| `P0["acanthosis_share_at_avg_risk"]` | 0.12, **0.028 since Change 3** | Acanthosis at average type 2 diabetes risk |
| `P0["acanthosis_or_per_sd"]` | 2.0 | Acanthosis odds x2 for each SD of type 2 diabetes risk (no published source; a marker setting) |
| `RR[...]` | see below | Relative risk on the person's own yearly onset chance |

### Relative risks and their sources

All are **author-defined (new, not yet physician-reviewed)**. Hazard and odds ratios are used as
relative risks, and factors multiply (assumed independent). Several sources measure a dose
(per 50 g/day, per 2 h/day, highest quartile) and are applied here to a yes/no flag: that is an
approximation, stated for each row. Each number was checked against the paper's abstract or an
indexed summary (not the full text); full citations, including page numbers, are to be completed
from the papers before the thesis.

| Disease | Risk factor (flag in the generator) | RR | Source and what the number is |
|---|---|---|---|
| CVD | current smoker | 1.63 | Banks E, et al. Tobacco smoking and risk of 36 cardiovascular disease subtypes. *BMC Med* 2019;17:128. doi:10.1186/s12916-019-1351-4. Adjusted HR, any major CVD, current vs never smokers. |
| CVD | secondhand smoke | 1.25 | He J, et al. Passive smoking and the risk of coronary heart disease: a meta-analysis of epidemiologic studies. *N Engl J Med* 1999;340. Pooled RR, exposed vs unexposed non-smokers (95% CI 1.17-1.32). |
| CVD | high-salt diet | 1.14 | Strazzullo P, et al. Salt intake, stroke, and cardiovascular disease: meta-analysis of prospective studies. *BMJ* 2009;339:b4567. Pooled RR, total CVD, higher vs lower salt (95% CI 0.99-1.32: not significant). |
| CVD | activity below 150 min/week (`activity_min_week` Moderate or worse) | 1.16 | Sattelmair J, et al. Dose response between physical activity and risk of coronary heart disease: a meta-analysis. *Circulation* 2011;124:789-95. 150 min/week vs none: RR 0.86, so 1/0.86. |
| T2D | current smoker | 1.37 | Pan A, et al. Relation of active, passive, and quitting smoking with incident type 2 diabetes. *Lancet Diabetes Endocrinol* 2015;3:958-67. doi:10.1016/S2213-8587(15)00316-2. Pooled RR (95% CI 1.33-1.42). |
| T2D | former smoker | 1.14 | Same source. Pooled RR, former smokers (95% CI 1.10-1.18). |
| T2D | processed diet | 1.19 | Micha R, Wallace SK, Mozaffarian D. Red and processed meat consumption and risk of incident coronary heart disease, stroke, and diabetes mellitus. *Circulation* 2010;121. RR per 50 g/day processed meat (applied to the yes/no flag). |
| T2D | low home activity | 1.35 | Smith AD, et al. Physical activity and incident type 2 diabetes mellitus: a systematic review and dose-response meta-analysis. *Diabetologia* 2016;59:2527-45. doi:10.1007/s00125-016-4079-0. 150 min/week: 26% lower risk, so 1/0.74. |
| T2D | sedentary 4 h/day or more (`sedentary_hours` Moderate or worse) | 1.20 | Grøntved A, Hu FB. Television viewing and risk of type 2 diabetes, cardiovascular disease, and all-cause mortality: a meta-analysis. *JAMA* 2011;305(23). RR per 2 h/day of TV (applied to the yes/no flag). |
| T2D | history of gestational diabetes | 9.51 | Vounzoulaki E, et al. Progression to type 2 diabetes in women with a known history of gestational diabetes: systematic review and meta-analysis. *BMJ* 2020;369:m1361. Pooled RR (95% CI 7.14-12.67). |
| Asthma | allergic rhinitis | 3.53 | Shaaban R, et al. Rhinitis and onset of asthma: a longitudinal population-based study. *Lancet* 2008;372:1049-57. Adjusted RR, adult-onset asthma. |
| Asthma | indoor mold | 1.29 | Quansah R, et al. Residential dampness and molds and the risk of developing asthma: a systematic review and meta-analysis. *PLoS One* 2012;7:e47526. Pooled OR, visible mold (95% CI 1.04-1.60). |
| Asthma | secondhand smoke | 1.97 | Jaakkola MS, Piipari R, Jaakkola N, Jaakkola JJK. Environmental tobacco smoke and adult-onset asthma: a population-based incident case-control study. *Am J Public Health* 2003;93(12):2055-60. doi:10.2105/ajph.93.12.2055. **Checked against the full text (Nov 6):** Table 3, adjusted OR for total exposure at home and at work in the past 12 months, 1.97 (95% CI 1.19-3.25), adults 21-63 who never smoked (239 cases, 487 controls). Not in the abstract, which gives work 2.16 (1.26-3.72) and home 4.77 (1.29-17.7). Adjusted for lifetime exposure instead: 1.66 (0.99-2.78). The generator applies it to everyone with the flag, smokers included (approximation). |
| COPD | current smoker | 3.51 | Forey BA, Thornton AJ, Lee PN. Systematic review with meta-analysis of the epidemiological evidence relating smoking to COPD, chronic bronchitis and emphysema. *BMC Pulm Med* 2011;11:36. Pooled RR (95% CI 3.08-3.99). |
| COPD | former smoker | 2.35 | Same source. Pooled RR, ex-smokers (95% CI 2.11-2.63). |
| COPD | occupational dust | 1.22 | Sadhra S, et al. Occupational COPD and job exposure matrices: a systematic review and meta-analysis. *Int J Chron Obstruct Pulmon Dis* 2017;12. doi:10.2147/COPD.S125980. Pooled OR, airborne occupational pollutants (95% CI 1.18-1.27). |
| COPD | coal heating | 2.80 | Kurmi OP, et al. COPD and chronic bronchitis risk of indoor air pollution from solid fuel: a systematic review and meta-analysis. *Thorax* 2010;65(3):221. Pooled OR, solid fuel use (95% CI 1.85-4.0). |
| COPD | secondhand smoke | 1.55 | Eisner MD, et al. Lifetime environmental tobacco smoke exposure and the risk of chronic obstructive pulmonary disease. *Environ Health* 2005;4:7. OR, highest quartile of lifetime home exposure (95% CI 1.09-2.21; applied to the yes/no flag). |
| Depression | traumatic brain injury | 1.59 | Orlovska S, et al. Head injury as risk factor for psychiatric disorders: a nationwide register-based follow-up study of 113,906 persons with head injury. *Am J Psychiatry* 2014;171. IRR, depression (95% CI 1.53-1.65). |

**Left out (no relative risk, so no effect on onset):** alcohol use (the flag is *any* use, 50% of
adults; no source gives a risk for any use), TBI for anxiety (the source reports no anxiety
estimate), lifestyle for hypertension (hypertension uses frailty only), carbohydrate and sodium
bands (salt enters through the high-salt-diet flag), and former smoking for CVD.

---

## Change 2: physician decisions of Nov 5, 2026

### CVD label counts hypertension

The CVD outcome is a **cardiovascular event, or SBP >= 140 or DBP >= 90 on two visits** (the date
of the second such visit). Hypertension that started on or before the index date counts as
already ill, so that patient has no CVD training row. Code: `cvd_outcome_date` and `training_rows`
in Part E5. The 5-year window and the other rules of `label_for` are unchanged.

`visits.csv` has two new columns: `has_CV_event` and `has_HTN`. `has_CVD` = either. Hypertension
known before the first visit counts from the first visit; new hypertension counts from the second
visit with high BP.

**Population check (Notebook 2, Cell 3):** GBD counts heart disease + stroke only. The events are
checked against GBD on their own. If the whole label fails against GBD alone, it is compared with
GBD heart disease + stroke plus the FNRI hypertension figure (17-23%) instead. The range is
[the larger of the two lower limits, the sum of the two upper limits], because a union is at
least its larger part and at most the sum of its parts.

### BMI: Asian cutoffs

Overweight >= 23, obese >= 25. Places that used the 25 / 30 bands:

| Place | Before | Now |
|---|---|---|
| `01_syntgen.ipynb`, Part E4 (BMI band) | 18.5-24.9 / 25.0-29.9 / 30.0-40.0 | `BMI_BANDS` 18.5-22.9 / 23.0-24.9 / 25.0-40.0 |
| `ml/features.yaml` | range 12-60 only, no bands | no change needed |
| `02_synthval.ipynb` | range 12-60 only | no change needed |
| `backend/care_plan.py` add-on trigger `bmi_25_or_more` and the 12 approved care-plan templates ("BMI 25 or more (see open question on 23)") | 25 | **Not changed here.** The templates were approved on Nov 5 and are read-only in `med_db`. Changing the trigger needs new template versions and the physician's approval. |
| CDSS Data Dictionary (document, not in the repository) | 25 / 30 | to be updated by the document owner |

### LVEF and NT-proBNP optional; hospital-only values

LVEF, troponin I, NT-proBNP, PaO2 and PaCO2 are not required anywhere. None of them is a model
input (`ml/features.yaml` lists them under `not_stored`), there is no `med_db` column for them
(`database/08_lab_results.sql`), and the backend never asks for them. In the generator they are
`phys` (hospital-only, missing 93% of the time) and never `req`.

### Doctor review sample

Notebook 2 saves the new sample as `output/doctor_review_v2.csv`. `output/doctor_review.csv` holds
the 20 patients the physician rated on Nov 5 and is never overwritten (`save_review` refuses it).

---

## Change 3: Phase 0b (Nov 6, 2026)

### All four labels follow the physician-approved rules

The rules signed on Nov 5 are applied to the **recorded visits** (Part E1c, `abnormal_tests`,
`outcome_date`, `already_ill`, `training_rows`):

| Label | Rule | In the code |
|---|---|---|
| CVD | SBP >= 140 or DBP >= 90 on two visits, or a cardiovascular event | 2 abnormal visits, or the event date |
| T2D | HbA1c >= 6.5% or FPG >= 126 mg/dL on two tests | 2 abnormal tests; HbA1c and FPG from one blood sample count as two (ADA Standards of Care: two abnormal results "from the same sample or in two separate test samples") |
| CRD | FEV1/FVC below 70%, or a doctor diagnosis of asthma | 1 spirometry below 70%, or the asthma diagnosis date |
| NEURO | PHQ-9 >= 10 or GAD-7 >= 10 on two visits | 2 abnormal visits |

**Training label (decision Nov 6): the true onset.** `label` = 1 when the generator's onset (CVD:
an event or hypertension; T2D: diagnosed or not; CRD: asthma or COPD; NEURO: depression or GAD)
falls after the index date and within 5 years. From onset on, the values meet the approved rule
whenever they are measured, so the onset is the date the rule *would* be met if tested.

**Side analysis: `label_recorded`** (extra column in `training.csv`) = 1 when the rule is met on
the recorded visits after the index visit and within 5 years, or by the diagnosis the rule names
(CVD event, asthma). A disease that starts but is never recorded (no lab test, no spirometry, too
few visits before the data end) is 0 here. COPD has no diagnosis route, so it counts only through
spirometry. `label_recorded` is never 1 when `label` is 0 (tested). On the Nov 6 5,000-patient run
(recorded labels only) this gave CVD 227, T2D 81, CRD 89 and NEURO 33 positives, against 259, 146,
139 and 58 true onsets.

**Left out** of a disease's rows (not labelled 0), for both labels: anyone whose disease truly
began on or before the index date (undiagnosed type 2 diabetes and, for CVD, hypertension
included), and anyone whose index visit already meets the rule's cut-off (one abnormal reading is
enough to be left out). HbA1c and FPG from one blood sample count as two tests (ADA 2019).

**FEV1/FVC conflict (for the physician):** the Data Dictionary's Moderate band for FEV1/FVC
(60-69.9%) lies wholly below the 70% CRD cut-off. On the Change 1 data, 578 of 1,337 CRD rows
with spirometry had FEV1/FVC < 70% at the index visit, i.e. already met the CRD rule. The band is
not changed; instead FEV1/FVC stays in the Low band (72-85%) until asthma or COPD starts.

### Only label-defining values are capped before diagnosis

Before diagnosis, SBP, DBP, HbA1c, FPG, PHQ-9 and GAD-7 stay at most in Moderate and FEV1/FVC in
Low (`PRE_DX_CAP`), so no one meets a rule before the disease starts (tested). The 2-hour OGTT
(`pg_2h`) is not in the approved T2D rule but is held below 200 mg/dL the same way (decision
Nov 6). Every other value, BMI included, may drift into High before diagnosis.

### Lower-is-worse values (bug fix in Change 1)

For values where a lower number is worse (HDL, eGFR, SpO2, FEV1, FVC, FEV1/FVC, FEV1 % predicted,
PEF, sleep hours, LVEF, PaO2, ACT, vitamin B12, activity), Change 1 drew the value from the low end
of the band upward, so drifting up *inside* a band made the value healthier and it then jumped at
the band edge. Now the position is read from the healthy end of the band (`lower_is_worse`), so a
higher risk always means a worse or equal value (tested). The value distribution of each band is
unchanged.

### Acanthosis and allergic rhinitis calibrated to the approved shares

Report-only (Change 1) gave acanthosis 32.1% in T2D / 17.2% in others (approved 40% / 5%) and
allergic rhinitis 40.1% in asthma / 16.3% in others (approved 60% / 15%).

Risk alone cannot reach 40% vs 5%: future and present cases (mean T2D risk z 1.5) and others (mean
-0.05) overlap too much (SD 1.9); solving for the two settings ran the odds ratio to infinity. So
the people whose disease **began before the window** get a published share of their own. All of
them are left out of that disease's training rows (the index date is never before the window
start), so the flag still never tells the model about a future diagnosis (tested:
`acanthosis_chance` gives the same value whether a later diabetes diagnosis is present or not;
rhinitis is added only for asthma before the window). Acanthosis is a T2D model input only, and
allergic rhinitis a CRD model input only.

This is **calibration to physician-approved targets**, like `prevalence_factor` to GBD. It was
solved once from a 5,000-patient run (patients only, seed 20261101) and not changed for any model
result. All four settings are **author-defined (new, not yet physician-reviewed)**:

| Setting | Value | Published range | Source |
|---|---|---|---|
| `P0["acanthosis_share_at_avg_risk"]` | 0.028 | (calibrated to approved 5% in others) | - |
| `P0["acanthosis_share_if_t2d_before_window"]` | 0.49 | about 20-67% of adults with T2D, by population | Bahadursingh S, et al. Acanthosis nigricans in type 2 diabetes: prevalence, correlates and potential as a simple clinical screening tool. *Diabetol Metab Syndr* 2014;6:77 (52.7%, 95% CI 47.2-58.3; the paper cites up to two thirds in North India, under 20% in Nigeria); a Lahore study (2022) found 25.2% |
| `P0["allergic_rhinitis_share"]` | 0.175 | 10-32% of adults in Asia-Pacific | Pawankar R, Bunnag C, Khaltaev N, Bousquet J. Allergic rhinitis and its impact on asthma in Asia Pacific and the ARIA update 2008. *World Allergy Organ J* 2012;5(Suppl 3):S212-7 |
| `P0["allergic_rhinitis_added_if_asthma_before_window"]` | 0.46 | rhinitis in 60-80% of people with asthma | Same source ("60% to 80% of patients with asthma had rhinitis symptoms"). With the pre-disease draw, 66.9% of asthma before the window has rhinitis |

Result on the calibration run: acanthosis 40.5% in T2D / 5.1% in others; allergic rhinitis 59.6%
in asthma / 14.9% in others. `acanthosis_or_per_sd` (2.0) is unchanged.

### BMI calibrated to FNRI 2021 ENNS (source approved Nov 6)

Source: DOST-FNRI (2022). *2021 Expanded National Nutrition Survey: National Results
Dissemination.* Measured BMI, last visit compared.

| BMI (kg/m2) | Adults 20-59 (Asia-Pacific bands) | Adults 60+ (WHO cut-offs) |
|---|---|---|
| < 18.5 (chronic energy deficiency) | **7.2%** (95% CI 6.4-8.1) | **11.8%** |
| 18.5-22.9 | **34.3%** (the rest) | - |
| 23.0-24.9 | **18.3%** | - |
| 18.5-24.9 | (52.6%) | **56.6%** (the rest) |
| 25.0-29.9 | **30.2%** | **25.4%** |
| >= 30 | **10.0%** | **6.2%** |

(20-59 by sex: 23.0-24.9 men 18.8%, women 17.8%; >= 25 men 35.7%, women 43.9%; >= 30 men 7.3%,
women 12.2%. The 2023 NNS gives >= 23 57.1%, >= 25 39.8%, < 18.5 7.9% for 20-59.)

**Method (like `prevalence_factor`):** BMI gets its own starting bands instead of
`baseline_mod_share` (`start_mod`, `start_high`), the Low band gets an underweight part
(16.0-18.4, `underweight_of_low`) and the High band a part at 30 or more (`obese30_of_high`), one
set per age group (age at the window start, fixed per person, so BMI does not jump at 60). Drift
with type 2 diabetes risk and High after diabetes starts are unchanged. Solved once
(`calib_bmi.py` logic: each patient's position at the last visit, from the notebook's own
functions; seed 20261101, 5,000 patients) so that the last visit matches the targets, never for a
model result. 60+ uses the 20-59 `start_mod` (the source has no 23.0 split for 60+). All values
are **author-defined (new, not yet physician-reviewed)**; the lower end 16.0 and the upper end 40
are author-defined.

| `BMI_CAL` | 20-59 | 60+ |
|---|---|---|
| `start_mod` | 0.03 | 0.03 |
| `start_high` | 0.322 | 0.169 |
| `underweight_of_low` | 0.242 | 0.333 |
| `obese30_of_high` | 0.24 | 0.21 |

Both age groups match every ENNS bin since BMI is no longer forced to High by diabetes (next
section). (Before that change, 33.9% of the synthetic 60+ had diabetes and therefore BMI >= 25,
above the 31.6% target, so 60+ could not be calibrated.)

### Causes of a disease are not forced to High by its diagnosis (decision Nov 6)

High BMI is a cause of type 2 diabetes, not a consequence. Before, every value driven by a
disease (`drv`) jumped to High once that disease began. Now the values below keep following the
person's own path after diagnosis (drift with their risk; lifestyle values: their baseline),
`NOT_FORCED_AFTER_DX`, tested. Every value driven by a disease was classified:

| Value (driven by) | Cause or consequence | After diagnosis |
|---|---|---|
| BMI, waist, waist-hip ratio (T2D) | cause: adiposity | **own path** |
| Total cholesterol, LDL, HDL (CVD) | cause / risk marker: lipids are not raised by a CVD event | **own path** |
| Sodium, activity (CVD/HTN), carbohydrates, sedentary hours (T2D) | cause: lifestyle | **own path** (baseline) |
| HbA1c, FPG, OGTT (T2D); SBP, DBP (HTN); FEV1/FVC (CRD); PHQ-9, GAD-7 (NEURO) | define the disease | High |
| Insulin, C-peptide, triglycerides, uric acid, UACR (T2D) | consequence of insulin resistance / kidney damage (also markers) | High |
| Heart rate, hs-CRP, creatinine, eGFR, LVEF, troponin, NT-proBNP (CVD) | consequence (or both) | High |
| Spirometry, SpO2, eosinophils, FeNO, IgE, mMRC, ACT, CAT, PaO2, PaCO2, exacerbations (CRD) | features of the disease | High |
| PSQI, sleep hours (NEURO) | symptoms | High (neuro episode rules) |

Uric acid, triglycerides, heart rate, hs-CRP and kidney values are both risk markers and
consequences; they were kept as consequences. For the physician to confirm.

**High-band pile-up (fix to Change 3):** with the cap lifted, a value that drifted past the High
band stayed at the band's maximum (e.g. BMI 40, waist 125 cm). Now, past the start of High, the
position nears the top of the band gradually (`1 - exp(-(position - 2))`), so values spread
across the band. Label-defining values never reach High before diagnosis, so they are not
affected.

### Final run (Nov 6)

Notebook 1 writes 20,000 patients with seed 20261101 to `output/` and 20,000 with seed 20261102
(author-defined) to `output_seed2/` (`SYNTH_N=5000` in the environment gives the quick test run).
Notebook 2 checks either folder (`SYNTH_DIR=output_seed2`); the doctor review sample comes from
`output/` only. `train_models.ipynb` uses `output_seed2/` as its external check and, besides the
chosen model, reports XGBoost for all four diseases (Cell 7b, comparison only; the model choice
is a group decision).

### Results of the final run (20,000 patients, two seeds)

| | Seed 20261101 (`output/`) | Seed 20261102 (`output_seed2/`) |
|---|---|---|
| Hard rules (10 rules, 19 checks) | 0 failing rows | 0 failing rows |
| Population checks | 18 / 18 PASS | 18 / 18 PASS |
| Family multiplier measured back (target 1.7-2.3) | 1.94 | 2.02 |
| Labels, true onset (CVD / T2D / CRD / NEURO) | 971 / 606 / 573 / 240 | 1,008 / 632 / 564 / 247 |
| `label_recorded` | 861 / 280 / 374 / 147 | 897 / 260 / 338 / 165 |
| Leak check, logistic regression on bands only (CVD / T2D / CRD / NEURO) | 0.69 / 0.66 / 0.66 / 0.62 | 0.69 / 0.66 / 0.68 / 0.63 |

Test counts in the saved notebook outputs: Notebook 1 109 passed, Notebook 2 31 passed (both seeds).

### Regenerating the second seed

`ml/synthetic/output_seed2/*.csv` are not committed (git-ignored): Notebook 1 rebuilds them exactly,
because every patient gets its own random stream from (seed, patient id). From `ml/synthetic/`:

    uv run --group ml jupyter nbconvert --to notebook --execute --inplace 01_syntgen.ipynb
    SYNTH_DIR=output_seed2 uv run --group ml jupyter nbconvert --to notebook --execute 02_synthval.ipynb \
        --output-dir output_seed2 --output 02_synthval_seed2.ipynb

The first writes 20,000 patients with seed 20261101 to `output/` and with seed 20261102 to
`output_seed2/` (about 8 minutes); the second checks the second seed. The executed check,
`output_seed2/02_synthval_seed2.ipynb`, is committed as the record of that run.

### ipytest

The `%%ipytest` cells collected no tests when run from `ml/synthetic/` (the folder was a Python
package, so pytest imported ipytest's empty temporary file under another name). Fixed in its own
commit: `ml/synthetic/__init__.py` removed; `raise_on_error=True`, so a failing or empty test cell
stops the notebook.

---

## Change 4: NEURO risk factors (Phase 0c, Nov 6, 2026)

Before, depression had one risk factor (traumatic brain injury, 1.59) and GAD none, so NEURO onset
was almost only age, sex, family history and frailty. Added (decisions Nov 6); every value below
is **author-defined (new, not yet physician-reviewed)**:

| Disease | Factor (flag in the generator) | RR | Source |
|---|---|---|---|
| Depression | insomnia: baseline sleep hours in the Moderate band or worse, drawn before any disease (`insomnia`, share = `baseline_mod_share`, 20%) | 2.83 | Hertenstein E, Feige B, Gmeiner T, Kienzler C, Spiegelhalder K, Johann A, Jansson-Fröjmark M, Palagini L, Rücker G, Riemann D, Baglioni C. Insomnia as a predictor of mental disorders: a systematic review and meta-analysis. *Sleep Med Rev* 2019;43:96-105. doi:10.1016/j.smrv.2018.10.006. Pooled OR, depression, 10 studies (95% CI 1.55-5.17). Checked against the paper's abstract (PubMed 30537570); the full text is subscription-only, so the table number is to be added |
| GAD | insomnia (same flag) | 3.23 | Same paper. Pooled OR, anxiety, 6 studies (95% CI 1.52-6.85) |
| Depression | activity below 150 min/week (`low_activity`, existing flag) | 1.20 | Schuch FB, et al. Physical activity and incident depression: a meta-analysis of prospective cohort studies. *Am J Psychiatry* 2018;175(7):631-48. High vs low activity AOR 0.83 (0.79-0.88), so 1/0.83 |
| GAD | activity below 150 min/week | 1.35 | Schuch FB, et al. Physical activity protects from incident anxiety: a meta-analysis of prospective cohort studies. *Depress Anxiety* 2019;36(9):846-58. AOR 0.74 (0.62-0.88), so 1/0.74 |
| Depression | current smoker | 1.62 | Luger TM, Suls J, Vander Weg MW. How robust is the association between smoking and depression in adults? A meta-analysis using linear mixed-effects models. *Addict Behav* 2014;39(10):1418-29. Prospective studies, smokers vs never smokers, OR 1.62 (1.10-2.40) |
| Depression | obesity, BMI >= 30 on 1 July of the year (Luppino's definition), from the person's own BMI path | 1.55 | Luppino FS, et al. Overweight, obesity, and depression: a systematic review and meta-analysis of longitudinal studies. *Arch Gen Psychiatry* 2010;67(3):220-9. Unadjusted OR 1.55 (1.22-1.98) |
| Depression | type 2 diabetes, from the year after its onset year | 1.15 | Mezuk B, Eaton WW, Albrecht S, Golden SH. Depression and type 2 diabetes over the lifespan: a meta-analysis. *Diabetes Care* 2008;31(12):2383-90. RR 1.15 (1.02-1.30) |

Left out: childhood maltreatment (not collected by the app; sensitive), smoking and obesity for
GAD (no pooled prospective estimate found), diabetes for GAD.

**Odds ratios used as relative risks.** Hertenstein, Schuch, Luger and Luppino report odds ratios.
An odds ratio is close to the relative risk when the outcome is rare in the study's follow-up
(Zhang J, Yu KF. What's the relative risk? *JAMA* 1998;280(19):1690-1). Here the yearly onset
chance of depression and GAD is well under 1% at every age and the 5-year label rate is 1.2-1.3%,
so with OR 1.2-3.2 the corresponding RR is within a few percent of the OR (Zhang-Yu:
RR = OR / (1 - p0 + p0 x OR), with p0 = the outcome risk in the unexposed). The same reasoning
covers the odds ratios already used in Change 1.

**How they enter.** Insomnia, low activity and smoking are fixed flags drawn before any disease and
join the personal multiplier M (its average stays 1; tested). Diabetes and obesity change over time,
so they multiply the depression hazard year by year (`onset_tv_mult`): diabetes only from the year
after its onset year (the same order as the family rule), obesity from the BMI path on 1 July
(which drifts with type 2 diabetes risk, never with a diagnosis). The obesity factor is divided by
its population average (`OBESITY_SHARE` 0.10, ENNS 2021 adults 20-59 with BMI >= 30) so the
calibrated depression rates keep their meaning; diabetes is not (its effect on the average is
about 1-2%). The value drift (`risk_z`) uses the same factors, only from the past.

**Cause before consequence (sleep rule, approved Nov 6).** Insomnia is a cause, and sleep
disturbance is also a symptom of depression and GAD. So: (1) insomnia = the baseline sleep band
(Moderate or worse), drawn before any disease, and PSQI's baseline band is tied to it; (2) before
diagnosis sleep hours and PSQI stay at that baseline (no drift with NEURO risk); (3) after
depression or GAD starts they go High, and the 2-year episode rule is unchanged. A NEURO diagnosis
does not change any value marked as a cause (activity, BMI, smoking; tested).

**Results, 5,000 patients (first 5,000 of the Change 3 data before, same seed after):** PSQI High
in people without any disease 33% -> 0%, sleep hours High 32% -> 0% (Moderate about 20%, the
insomnia share). PHQ-9 >= 10 at the last visit 3.7% -> 3.5% (target 3.3-9.9%, PASS). NEURO label
56 -> 62 (1.2% -> 1.3%). All hard rules and 18/18 population checks pass. The band-only leak check
for NEURO rose 0.58 -> 0.67: insomnia is now a real cause drawn before any disease and shows in the
baseline sleep band; the no-leak test (two copies differing only in the diagnosis year) still passes.

**Results, final run (20,000 patients; seed 20261101 / seed 20261102):** hard rules 0 failures;
18/18 population checks PASS (PHQ-9 >= 10: 3.8% / 3.7%); family multiplier measured back 1.97 / 1.99
(depression 1.94 / 1.95, GAD 2.28 / 2.79); NEURO label 243 / 247 (1.3%); PSQI and sleep hours High
in people without disease 0% (Moderate 18-19%). GAD ever diagnosed by the last visit (Notebook 2,
Cell 3d, informational): all adults 0.9% / 0.7%, adults 70+ 1.7% / 1.0% (lifetime target 1.6%).

Leak check, NEURO, bands only: 0.666 / 0.677 with all bands; 0.645 / 0.655 without the sleep-hours
and PSQI bands (before Change 4: 0.616 / 0.628). So the rise is not only insomnia: the generator's
own NEURO risk is now better explained (oracle 0.678 -> 0.716, without frailty 0.628 -> 0.676), and
PHQ-9 and GAD-7 drift with that risk (GAD-7 Moderate in future cases 54% vs non-cases 41%; before
49% vs 47%). Not a leak: values before diagnosis do not depend on the diagnosis year (tested).

---

## Questions for the physician

1. **FEV1/FVC band.** The Data Dictionary's Moderate band for FEV1/FVC (60-69.9%) lies wholly
   below the 70% CRD cut-off, so anyone "Moderate" already meets the CRD rule. On the Change 1
   data, 578 of 1,337 CRD rows with spirometry had FEV1/FVC < 70% at the index visit. Workaround
   (kept, decision Nov 6): FEV1/FVC stays in the Low band (72-85%) until asthma or COPD starts.
   Should the Moderate band be 70-71.9%, or should the CRD rule use a different cut-off?
2. **PSQI and sleep hours in people without disease.** In the Nov 6 5,000-patient run, 32% of the
   2,742 people who never get any disease had PSQI and sleep hours in the High band at their first
   visit (they drifted with depression and anxiety risk). Since Change 4 they keep their baseline
   until a NEURO diagnosis: 0% High, about 20% Moderate (the insomnia share). Is that plausible?
3. **CRD values in people without disease.** In the same people, most CRD values (FEV1, FVC,
   FEV1 % predicted, PEF, SpO2, eosinophils, FeNO, IgE, mMRC) were High in about 17-22%. Is that
   plausible?
4. **Cause or consequence.** BMI, waist, waist-hip ratio, lipids and lifestyle values are no
   longer pushed to High by a diagnosis (they are causes). Triglycerides, uric acid, heart rate,
   hs-CRP and kidney values are still pushed to High (treated as consequences). Is that split
   right?
5. **GAD lifetime target.** Is the GAD lifetime target of 1.6% appropriate? (Approved on Nov 5;
   GBD's "anxiety disorders" covers more than GAD.)
6. **Sleep in people without disease (since Change 4).** With the sleep rule, 0% of people without
   any disease have High PSQI or sleep hours (about 19% Moderate, the insomnia share). Is that
   realistic?
