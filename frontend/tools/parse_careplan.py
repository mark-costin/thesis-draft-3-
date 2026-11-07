"""Convert careplan docx into careplan_data.json (12 templates)."""
import json, re, sys
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

d = docx.Document(sys.argv[1])
templates, cur, last_tbl_kind = {}, None, None
reuse = {}
for el in d.element.body.iterchildren():
    if el.tag.endswith('}p'):
        t = Paragraph(el, d).text.strip()
        m = re.fullmatch(r"(CVD|T2D|CRD|NEURO)-(LOW|MODERATE|HIGH)", t)
        if m:
            cur = t
            templates[cur] = {"id": t, "disease": m.group(1), "tier": m.group(2).title(), "items": [], "addons": [], "reuse": None}
        elif cur and t.startswith("Add-ons:"):
            mm = re.search(r"(A\d)(?: to |, | and )?(A\d)? from ((?:CVD|T2D|CRD|NEURO)-\w+)", t)
            nums = re.findall(r"A(\d)", t.split("from")[0]); src = re.search(r"from ((?:CVD|T2D|CRD|NEURO)-[A-Z]+)", t).group(1)
            lo, hi = int(nums[0]), int(nums[-1])
            templates[cur]["reuse"] = {"from": src, "ids": [f"A{i}" for i in range(lo, hi + 1)]}
    elif el.tag.endswith('}tbl') and cur:
        rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in Table(el, d).rows]
        head = rows[0]
        if head[1] == "Category":
            for r in rows[1:]:
                templates[cur]["items"].append({"n": int(r[0]), "category": r[1], "text": r[2], "why": r[3], "source": r[4], "basis": r[5], "goal": r[6].lower() == "yes"})
        elif head[1] == "Trigger":
            for r in rows[1:]:
                templates[cur]["addons"].append({"id": r[0], "trigger": r[1], "text": r[2], "why": r[3], "source": r[4], "basis": r[5], "goal": r[6].lower() == "yes"})
for t in templates.values():
    if t["reuse"]:
        src = templates[t["reuse"]["from"]]["addons"]
        t["addons"] = [a for a in src if a["id"] in t["reuse"]["ids"]]
    del t["reuse"]
json.dump({"version": "1.0", "source": "careplan_2026-10-27", "templates": templates}, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
for k, t in templates.items():
    print(k, len(t["items"]), [a["id"] for a in t["addons"]])
print(json.dumps([ (a["id"],a["trigger"]) for k in ("CRD-LOW","CVD-LOW","T2D-LOW") for a in templates[k]["addons"]], indent=0))
