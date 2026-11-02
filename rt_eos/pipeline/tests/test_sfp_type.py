"""The SFP type model beside the port model (port_pattern.refine_sfp_types).

The SFP cage finder reads the cage type (SFP / QSFP / OSFP); port_type_finder.pt reads every
port. A cage settles the type of the SFP family port it overlaps most, a cage
the port model missed is added, and RJ45 or console ports are never touched.
"""

from pipeline.port_pattern import _bucket_and_index, refine_sfp_types


def det(cls, box, conf=0.9):
    return {"class_name": cls, "confidence": conf, "bbox": list(box)}


def test_a_cage_settles_the_type_of_the_port_it_overlaps():
    typed = [det("SFP", (0, 0, 40, 30)), det("SFP", (50, 0, 90, 30)), det("RJ45", (100, 0, 130, 30))]
    refine_sfp_types(typed, [det("QSFP", (2, 1, 41, 31), 0.8), det("OSFP", (101, 0, 131, 30), 0.9)])
    assert [t["class_name"] for t in typed] == ["QSFP", "SFP", "RJ45"]
    assert typed[0]["sfp_type"] == "QSFP" and typed[0]["sfp_confidence"] == 0.8
    assert "sfp_type" not in typed[2]


def test_a_cage_the_port_model_missed_is_added_and_bucketed_as_sfp():
    typed = [det("RJ45", (0, 0, 30, 30))]
    refine_sfp_types(typed, [det("OSFP", (200, 0, 260, 30))])
    assert len(typed) == 2 and typed[1]["class_name"] == "OSFP"
    for t in typed:
        t["status"] = "unknown"
    buckets = _bucket_and_index(typed)
    assert [p["class_name"] for p in buckets["sfp"]] == ["OSFP"]
    assert buckets["sfp"][0]["sfp_type"] == "OSFP"


def test_no_cages_changes_nothing():
    typed = [det("SFP", (0, 0, 40, 30))]
    assert refine_sfp_types(typed, []) is typed
    assert typed[0]["class_name"] == "SFP" and "sfp_type" not in typed[0]
