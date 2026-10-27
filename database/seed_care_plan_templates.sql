-- seed_care_plan_templates.sql
-- Purpose: Loads the 12 care plan templates from careplan_oct27.docx (version 1.0) as DRAFTS.
-- Generated from the Word file; edit the Word file and regenerate rather than editing by hand.
--
-- Run as postgres AFTER 19_care_plan_templates.sql. Not numbered on purpose, so Docker
-- does not load unreviewed advice automatically.
-- Drafts load with reviewed_by/reviewed_on NULL and is_active = false, so the app shows none
-- of them yet. After the physician signs a template (Section 7 of the care plan):
--     UPDATE public.care_plan_templates
--        SET reviewed_by = 'Dr. <name>, <specialty>', reviewed_on = DATE '<yyyy-mm-dd>', is_active = true
--      WHERE disease_domain = 'CVD' AND risk_level = 'Low' AND version = '1.0';
-- From then on that row is read-only; any change is a new version (e.g. '1.1').

BEGIN;

-- CVD-LOW: 7 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your blood pressure checked in a clinic at least once a year.",
      "why": "Regular checks find high blood pressure before it causes harm.",
      "source": "PHEx 2021, Sec. 3.4",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Good sleep and less stress support healthy blood vessels.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 7,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Ask your doctor about getting a lipid profile test.",
      "why": "Heart disease in your family makes checking blood fats more important.",
      "source": "PHEx 2021, Sec. 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CVD-MODERATE: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a follow-up visit in 3 to 6 months to review your heart health.",
      "why": "A review checks whether your lifestyle changes are working.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Check your blood pressure at home for 3 to 7 days before your visit.",
      "why": "Home readings show your usual blood pressure outside the clinic.",
      "source": "ISH 2020, Sec. 4 and Fig. 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Good sleep and less stress support healthy blood vessels.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Have a yearly blood pressure check and ask your doctor about a lipid profile.",
      "why": "Heart disease in your family calls for closer tracking.",
      "source": "PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CVD-HIGH: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days to confirm your blood pressure and check your lipid profile and fasting blood sugar.",
      "why": "Your results suggest a higher risk of heart disease that needs a medical check.",
      "source": "ISH 2020, Sec. 4; PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Record home blood pressure readings for 7 days before your visit.",
      "why": "Repeat readings help your doctor see if your pressure is high most of the time.",
      "source": "ISH 2020, Sec. 4 and Table 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Managing stress and rest helps protect your heart.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Have a yearly blood pressure check after your doctor visit and keep your lipid profile up to date.",
      "why": "Heart disease in your family calls for closer tracking.",
      "source": "PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-LOW: 5 base items, 6 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your fasting blood sugar or HbA1c checked at your yearly health visit.",
      "why": "Early testing finds blood sugar changes before symptoms appear.",
      "source": "PHEx 2021, Sec. 3.3; ADA 2019, Sec. 2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "fhrs_t2d_band_3_or_4",
      "trigger": "T2D family history band 3 or 4",
      "action": "Ask your doctor about earlier fasting blood sugar screening because of diabetes in your family.",
      "why": "Diabetes in your family raises your lifetime risk.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-MODERATE: 6 base items, 5 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a follow-up in 3 to 6 months to review your blood sugar risk.",
      "why": "A review checks whether your diet and activity changes are working.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Ask your doctor about testing your fasting blood sugar or HbA1c.",
      "why": "Tests show whether your blood sugar is moving toward prediabetes.",
      "source": "ADA 2019, Tables 2.2 and 2.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-HIGH: 6 base items, 5 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days for a fasting blood sugar or HbA1c test.",
      "why": "Your results suggest a higher risk of diabetes that needs a lab check.",
      "source": "ADA 2019, Table 2.2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "If the result is high, expect a second test to confirm it.",
      "why": "A second test makes the result reliable.",
      "source": "ADA 2019, Table 2.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-LOW: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your breathing checked at your yearly doctor visit.",
      "why": "Regular checks find breathing changes early.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Watch for breathing symptoms such as long-lasting cough, wheezing, or shortness of breath.",
      "why": "Noticing changes early helps you get advice in time.",
      "source": "GOLD 2020, Ch. 1 and 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4",
      "trigger": "Allergic rhinitis, childhood asthma history, or CRD family history band 3 or 4 (any)",
      "action": "Ask your doctor whether you need a lung function test.",
      "why": "Nasal allergies, childhood asthma, and lung disease in your family raise your risk.",
      "source": "GINA 2026; GOLD 2020, Ch. 1; CDSS Data Dictionary (FHRS)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-MODERATE: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a doctor visit in 3 to 6 months to review your breathing.",
      "why": "A review catches new breathing symptoms early.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Keep track of any lasting cough, phlegm, or wheezing and tell your doctor at your visit.",
      "why": "A symptom record helps your doctor decide if tests are needed.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4",
      "trigger": "Allergic rhinitis, childhood asthma history, or CRD family history band 3 or 4 (any)",
      "action": "Ask your doctor whether you need a lung function test.",
      "why": "Nasal allergies, childhood asthma, and lung disease in your family raise your risk.",
      "source": "GINA 2026; GOLD 2020, Ch. 1; CDSS Data Dictionary (FHRS)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-HIGH: 8 base items, 2 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days and ask about a lung function test [spirometry] to check for asthma or COPD.",
      "why": "Your results suggest a higher risk of lung disease that needs testing.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Track your breathing symptoms and note patterns of cough or breathlessness.",
      "why": "A symptom record helps your doctor read your test results.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-LOW: 8 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Retake the PHQ-9 (mood) and GAD-7 (anxiety) questionnaires at your yearly check-up.",
      "why": "Regular tracking shows changes in mood and anxiety over time.",
      "source": "PHEx 2021, Sec. 3.15 (PHQ-9)",
      "basis": "Sourced (PHQ-9) / System rule (GAD-7, yearly)",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Learn about anxiety and how it differs from everyday worry.",
      "why": "Understanding anxiety helps you notice it early and manage it.",
      "source": "NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-MODERATE: 9 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "See a doctor",
      "action": "Schedule a follow-up in 3 to 6 months to review your emotional well-being.",
      "why": "A review checks whether self-care is helping your mood and anxiety.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 3,
      "category": "Check-up and tests",
      "action": "Retake the PHQ-9 and GAD-7 questionnaires in 3 to 6 months.",
      "why": "Re-screening shows whether mood and anxiety are getting better or worse.",
      "source": "PHEx 2021, Sec. 3.15 (PHQ-9)",
      "basis": "Sourced (PHQ-9) / System rule (GAD-7, interval)",
      "goal": true
    },
    {
      "no": 4,
      "category": "See a doctor",
      "action": "Ask your healthcare provider about guided self-help, a psychoeducational group, or talking therapy for low mood or anxiety.",
      "why": "Structured support gives practical tools for coping with low mood and worry.",
      "source": "NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 9,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-HIGH: 8 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days for a depression and anxiety assessment, and retake the PHQ-9 and GAD-7 before the visit.",
      "why": "Your results suggest a higher risk that needs a clinical check.",
      "source": "PHEx 2021, Sec. 3.15; NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced (assessment) / System rule (timeframe, GAD-7)",
      "goal": true
    },
    {
      "no": 3,
      "category": "See a doctor",
      "action": "Ask your healthcare provider about talking therapy, such as CBT for low mood or anxiety, or applied relaxation for anxiety.",
      "why": "Talking therapy gives practical tools to manage low mood and worry.",
      "source": "NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

COMMIT;
