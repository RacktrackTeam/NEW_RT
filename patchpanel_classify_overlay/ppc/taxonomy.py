"""Canonical patch-panel taxonomy: family -> leaf type.

The raw gallery folders use inconsistent names ("LC UPC" with a space,
"SC APC Connected" which names a *state* rather than a connector type).
Everything downstream speaks the canonical names defined here.
"""

# family -> canonical leaf types
TAXONOMY = {
    "Copper_Ethernet": ["RJ-45"],
    "Fiber_Optic": ["LC_UPC", "LC_APC", "SC_UPC", "SC_APC", "ST_Ports", "FC"],
    "Coaxial_RF": ["BNC", "RF_Coax", "F_type", "TNC"],
}

# raw gallery folder name -> canonical leaf
FOLDER_TO_LEAF = {
    "RJ-45": "RJ-45",
    "LC UPC": "LC_UPC",
    "LC_APC": "LC_APC",
    "SC_UPC": "SC_UPC",
    "SC APC Connected": "SC_APC",
    "ST_Ports": "ST_Ports",
    "FC": "FC",
    "BNC": "BNC",
    "RF_Coax": "RF_Coax",
    "F_type": "F_type",
    "TNC": "TNC",
}

LEAVES = [leaf for leaves in TAXONOMY.values() for leaf in leaves]
FAMILIES = list(TAXONOMY)

LEAF_TO_FAMILY = {
    leaf: fam for fam, leaves in TAXONOMY.items() for leaf in leaves
}

LEAF_IDX = {leaf: i for i, leaf in enumerate(LEAVES)}
FAMILY_IDX = {fam: i for i, fam in enumerate(FAMILIES)}


def family_of(leaf):
    return LEAF_TO_FAMILY[leaf]


def rel_path(leaf):
    """Output path for a leaf, e.g. 'Fiber_Optic/LC_APC'."""
    return "%s/%s" % (LEAF_TO_FAMILY[leaf], leaf)
