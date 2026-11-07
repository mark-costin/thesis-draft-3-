"""Philippine address lists for the Province > City or municipality > Barangay drop-downs (all sorted A to Z).

Data lives in ``data/ph_locations.json`` as ``{province: {city: [barangay, ...]}}``.
On first start, if the file does not yet hold the full list, the app downloads the official PSGC list
(Philippine Statistical Authority) once and saves it. That needs internet only that one time.
If the download fails, the small starter list in the file is used and the form says so.
"""
import json
import re
import urllib.request
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "ph_locations.json"
BASE = "https://psgc.gitlab.io/api"
FULL_BARANGAY_COUNT = 40000  # the PSGC list has about 42,000 barangays; fewer than this means the starter list


def natural_key(text: str):
    """A to Z, ignoring case, with numbers in order (Barangay 2 before Barangay 10)."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", text)]


def _get(path: str):
    with urllib.request.urlopen(f"{BASE}/{path}/", timeout=120) as r:
        return json.load(r)


def build_from_psgc() -> dict[str, dict[str, list[str]]]:
    provinces = {p["code"]: p["name"] for p in _get("provinces")}
    data: dict[str, dict[str, list[str]]] = {name: {} for name in provinces.values()}
    data["Metro Manila (NCR)"] = {}
    city_home: dict[str, tuple[str, str]] = {}
    for c in _get("cities-municipalities"):
        prov = provinces.get(c.get("provinceCode")) or ("Metro Manila (NCR)" if str(c.get("regionCode", "")).startswith("13") else None)
        if prov:
            data[prov].setdefault(c["name"], [])
            city_home[c["code"]] = (prov, c["name"])
    for b in _get("barangays"):
        key = b.get("cityCode") or b.get("municipalityCode")
        if key in city_home:
            prov, city = city_home[key]
            data[prov][city].append(b["name"])
    return data


def _count(data) -> int:
    return sum(len(b) for v in data.values() for b in v.values())


def _load() -> dict[str, dict[str, list[str]]]:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    if _count(data) < FULL_BARANGAY_COUNT:
        try:
            full = build_from_psgc()
            if _count(full) >= FULL_BARANGAY_COUNT:
                DATA_FILE.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
                return full
        except Exception as exc:  # offline, or the API layout changed: keep the starter list
            print(f"[locations] Could not download the PSGC address list ({exc}). Using the starter list.")
    return data


_DATA = _load()
LISTS_COMPLETE: bool = _count(_DATA) >= FULL_BARANGAY_COUNT
PROVINCES: list[str] = sorted(_DATA, key=natural_key)


def cities_for(province: str) -> list[str]:
    return sorted(_DATA.get(province, {}), key=natural_key)


def barangays_for(province: str, city: str) -> list[str]:
    return sorted(_DATA.get(province, {}).get(city, []), key=natural_key)
