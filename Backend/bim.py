"""Extract planar IFC space footprints (centimetres in the supplied IFC).

Only supported swept profiles are evaluated; unsupported geometry stays unknown.
Capacity is a planning assumption, never a certified regulatory capacity.
"""
import math
import re
from .config import AREA_PER_PERSON
from .etl import IFC_SPACE_RE, decode_ifc


def geometry(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    entities = dict(re.findall(r"#(\d+)\s*=\s*(.*?);", text))
    unit = re.search(r"IFCSIUNIT\(\*,\.LENGTHUNIT\.,([^,]+),\.METRE\.\)", text)
    scale = {".CENTI.": .01, ".MILLI.": .001, "$": 1.0}.get(unit.group(1) if unit else "", None)
    if scale is None:
        return {}

    def refs(value):
        return re.findall(r"#(\d+)", value)

    def polygon(key):
        points = []
        for ref in refs(entities.get(key, "")):
            raw = entities.get(ref, "")
            if not raw.startswith("IFCCARTESIANPOINT"):
                return None
            nums = re.findall(r"[-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?", raw)
            if len(nums) < 2:
                return None
            points.append((float(nums[0]), float(nums[1])))
        if len(points) < 4 or points[0] != points[-1]:
            return None
        return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, points[1:]))) / 2

    def area(key, depth=0):
        if depth > 6:
            return None
        raw = entities.get(key, "")
        if raw.startswith("IFCARBITRARYCLOSEDPROFILEDEF"):
            return polygon(refs(raw)[0])
        if raw.startswith("IFCARBITRARYPROFILEDEFWITHVOIDS"):
            values = [polygon(r) for r in refs(raw)]
            return values[0] - sum(values[1:]) if values and all(v is not None for v in values) else None
        if raw.startswith("IFCEXTRUDEDAREASOLID"):
            return area(refs(raw)[0], depth+1)
        if raw.startswith(("IFCPRODUCTDEFINITIONSHAPE", "IFCSHAPEREPRESENTATION")):
            for r in refs(raw):
                found = area(r, depth+1)
                if found is not None:
                    return found
        return None

    result = {}
    for raw in entities.values():
        match = IFC_SPACE_RE.search(raw)
        if not match:
            continue
        code = match.group(1).strip()
        references = refs(raw)
        footprint = area(references[-1]) if references else None
        if footprint is not None and footprint > 0:
            m2 = round(footprint * scale**2, 2)
            result[code] = (m2, max(1, math.floor(m2 / AREA_PER_PERSON)), "estimated_from_ifc_area")
    return result
