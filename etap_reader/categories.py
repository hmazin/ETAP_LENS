"""
Curated, human-friendly groupings of ETAP's ~870 raw SQL tables.

ETAP stores one "current" table per element type (e.g. Bus, Cable, XFMR2)
plus several "H1".."H8" history/revision-snapshot variants and "_R" rating
sub-tables for each. The categories below only reference the current tables;
the raw H/_R tables are still reachable through the "All Tables" browser.
"""

CATEGORIES = {
    "buses": {
        "label": "Buses",
        "description": "Bus/node elements - the connection points of the network.",
        "tables": ["Bus"],
    },
    "cables": {
        "label": "Cables & Lines",
        "description": "Cables, bus ducts, overhead lines, and transmission lines.",
        "tables": ["Cable", "BusDuct", "XLine", "OLH", "DistributionCable", "DistributionLine"],
    },
    "transformers": {
        "label": "Transformers",
        "description": "Two- and three-winding transformers, autotransformers, and phase-shifters.",
        "tables": ["XFMR2", "XFMR3", "XFMRAuto", "XFMRBooster", "ZigzagXfmr", "MVSST", "PXform", "CXForm",
                   "DistributionXFMR"],
    },
    "generators_sources": {
        "label": "Generators & Sources",
        "description": "Utility ties, synchronous/wind/solar generators, and other sources.",
        "tables": ["Utility", "SynGen", "WTGEN", "PVArray", "GRID", "MGSet", "Battery", "Inverter"],
    },
    "loads_motors": {
        "label": "Loads & Motors",
        "description": "Static loads, lumped loads, motors, panels, and other consuming equipment.",
        "tables": ["StaticLoad", "Lump", "IndMotor", "SynMotor", "Panel", "MOV", "VFD", "UPS", "Charger",
                   "DistributionSpotLoad", "DistributionDistributedLoad"],
    },
    "protective_devices": {
        "label": "Protective Devices",
        "description": "Circuit breakers, fuses, switches, reclosers, and protective relays.",
        "tables": ["HVCB", "LVCB", "DCCB", "Fuse", "DCFuse", "DistributionFuse", "Recloser",
                   "DistributionRecloser", "GroundSwitch", "SPSTSwitch", "SPDTSwitch", "DistributionSwitch",
                   "DistributionCB", "Contactor", "OverCurrentRelay", "VoltageRelay", "FrequencyRelay",
                   "Relay32", "ILSCB"],
    },
    "capacitors_reactive": {
        "label": "Capacitors & Reactors",
        "description": "Shunt capacitors, static var devices, and series reactors.",
        "tables": ["CAP", "DistributionCapacitor", "StaticVar", "Reactor", "SVControl"],
    },
    "instrumentation": {
        "label": "Meters & Instrumentation",
        "description": "Ammeters, voltmeters, and wattmeters placed in the model.",
        "tables": ["AmMeter", "VoltMeter", "WattMeter"],
    },
}

# Curated groupings for ETAP analysis result files (.SA1S / .SA2S, ...),
# which are plain SQLite databases with a different schema per study type.
# Keyed by "category_set" (see study_result.STUDY_EXTENSIONS).
STUDY_CATEGORIES = {
    "sc_duty": {  # .SA1S - ANSI short circuit duty studies
        "device_duty": {
            "label": "Device Duty (Detail)",
            "description": "Per-fault, per-device fault current contributions (1/2-cycle and interrupting).",
            "tables": ["SCDuty"],
        },
        "bus_duty_interrupting": {
            "label": "Bus Duty - Interrupting",
            "description": "Interrupting duty summary vs. rating for each protective device.",
            "tables": ["SCDSumInt"],
        },
        "bus_duty_momentary": {
            "label": "Bus Duty - Momentary",
            "description": "Momentary (close-and-latch) duty summary vs. rating for each protective device.",
            "tables": ["SCDSumMom"],
        },
        "alerts": {
            "label": "Alerts / Violations",
            "description": "Devices whose calculated duty exceeds their rating, and other warnings.",
            "tables": ["Alert", "Messages"],
        },
    },
    "sc_fault": {  # .SA2S - fault-current-by-type studies (LG/LL/LLG)
        "fault_lg": {
            "label": "Line-to-Ground Faults",
            "description": "Fault currents and voltages for single line-to-ground faults, per bus.",
            "tables": ["SCLG"],
        },
        "fault_ll": {
            "label": "Line-to-Line Faults",
            "description": "Fault currents and voltages for line-to-line faults, per bus.",
            "tables": ["SCLL"],
        },
        "fault_llg": {
            "label": "Line-to-Line-Ground Faults",
            "description": "Fault currents and voltages for double line-to-ground faults, per bus.",
            "tables": ["SCLLG"],
        },
        "fault_summary": {
            "label": "Fault Current Summary",
            "description": "Per-bus summary of fault current magnitude and source impedance across fault types.",
            "tables": ["SCLGSum1", "SCLGSum2"],
        },
        "clipping_current": {
            "label": "Clipping Current (Device Duty)",
            "description": "Symmetrical/asymmetrical current duty seen by each protective device.",
            "tables": ["ClippingCurrent"],
        },
        "alerts": {
            "label": "Alerts / Violations",
            "description": "Devices whose calculated duty exceeds their rating, and other warnings.",
            "tables": ["Alert", "Messages"],
        },
    },
    "load_flow": {  # .LF1S - balanced (positive-sequence) load flow studies
        "bus_results": {
            "label": "Bus Voltage & Loading",
            "description": "Per-bus voltage, MW/Mvar/MVA, and loading vs. rating.",
            "tables": ["BusLoadSummary"],
        },
        "branch_loading": {
            "label": "Branch Loading",
            "description": "Loading percentage and ampacity for cables, lines, and transformers.",
            "tables": ["LFSumBranch"],
        },
        "branch_losses": {
            "label": "Branch Losses",
            "description": "Real/reactive power loss and voltage drop across each branch.",
            "tables": ["LFSumLoss"],
        },
        "system_totals": {
            "label": "System Totals",
            "description": "System-wide swing, generation, load, and loss totals, and solution convergence.",
            "tables": ["LFSumTotal"],
        },
        "sources_loads": {
            "label": "Sources & Loads",
            "description": "Per-element operating power, current, and losses for sources and loads.",
            "tables": ["LFSourceLoad"],
        },
        "element_results": {
            "label": "Detailed Element Results",
            "description": "Raw per-element load flow solution values (voltage, power flow, tap position).",
            "tables": ["LFR"],
        },
        "violations": {
            "label": "Voltage Violations",
            "description": "Buses operating above (LFOver) or below (LFUnder) their voltage limits.",
            "tables": ["LFOver", "LFUnder"],
        },
        "alerts": {
            "label": "Alerts / Overloads",
            "description": "Devices whose calculated loading exceeds their rating, and other warnings.",
            "tables": ["Alert", "Messages"],
        },
    },
    "load_flow_unbalanced": {  # .UL1S - unbalanced (3-phase, per-phase A/B/C) load flow studies
        "bus_results": {
            "label": "Bus Voltage & Loading (3-Phase)",
            "description": "Per-bus, per-phase voltage, MW/Mvar/MVA, loading, and voltage unbalance factors.",
            "tables": ["BusLoadSummaryLF3PH"],
        },
        "branch_loading": {
            "label": "Branch Loading (3-Phase)",
            "description": "Per-phase loading percentage and ampacity for cables, lines, and transformers.",
            "tables": ["LFSumBranchLF3PH"],
        },
        "branch_losses": {
            "label": "Branch Losses (3-Phase)",
            "description": "Per-phase real/reactive power loss and voltage drop across each branch.",
            "tables": ["LFSumLossLF3PH"],
        },
        "system_totals": {
            "label": "System Totals (3-Phase)",
            "description": "System-wide swing, generation, load, and loss totals per phase, and convergence.",
            "tables": ["LFSumTotalLF3PH"],
        },
        "load_results": {
            "label": "Load Results",
            "description": "Per-load, per-phase voltage, current, power, and unbalance factors.",
            "tables": ["LF3PHLoadR"],
        },
        "pd_loading": {
            "label": "Protective Device Loading",
            "description": "Continuous-current loading percentage for breakers, fuses, and relays.",
            "tables": ["LF3PHPDR"],
        },
        "element_results": {
            "label": "Detailed Element Results",
            "description": "Raw per-element, per-phase load flow solution values.",
            "tables": ["LF3PHR"],
        },
        "violations": {
            "label": "Voltage Violations",
            "description": "Buses operating above (LFOver) or below (LFUnder) their voltage limits, per phase.",
            "tables": ["LFOverLF3PH", "LFUnderLF3PH"],
        },
        "alerts": {
            "label": "Alerts / Overloads",
            "description": "Devices whose calculated loading exceeds their rating, and other warnings.",
            "tables": ["Alert", "Messages"],
        },
    },
}

# Small "info" tables worth showing on the Overview page, keyed by table name
# -> friendly title. Whichever of these exist in a given loaded file are shown.
OVERVIEW_INFO_TABLES = {
    "ProjProps": "Project Properties",
    "ProjectRec": "Project Record",
    "ISCStudyCase": "Study Case Parameters",
    "ILFStudyCase": "Study Case Parameters",
    "ILFStudyCaseLF3PH": "Study Case Parameters",
    "Isummary": "Equipment Summary (as of this study)",
    "IsummaryLF3PH": "Equipment Summary (as of this study)",
    "LFSumTotal": "System Totals",
    "LFSumTotalLF3PH": "System Totals",
    "Headr": "Header",
}

# Tables that participate in bus connectivity, and which columns on them
# name the bus(es)/element(s) they connect to. Used by the Single Line view.
CONNECTIVITY_TABLES = {
    "Cable": ["FromBus", "ToBus"],
    "BusDuct": ["FromBus", "ToBus"],
    "XLine": ["FromBus", "ToBus"],
    "OLH": ["FromBus", "ToBus"],
    "XFMR2": ["FromBus", "ToBus"],
    "XFMR3": ["FromBus", "ToBus", "TertBus"],
    "Reactor": ["FromBus", "ToBus"],
    "GroundSwitch": ["FromBus", "ToBus"],
    "HVCB": ["FromElement", "ToElement"],
    "LVCB": ["FromElement", "ToElement"],
    "SPSTSwitch": ["FromElement", "ToElement"],
    "SPDTSwitch": ["FromElement", "ToElement"],
    "Fuse": ["FromElement", "ToElement"],
    "Recloser": ["FromElement", "ToElement"],
    "Contactor": ["FromElement", "ToElement"],
    "Utility": ["Bus"],
    "SynGen": ["Bus"],
    "WTGEN": ["Bus"],
    "PVArray": ["Bus"],
    "StaticLoad": ["Bus"],
    "Lump": ["Bus"],
    "IndMotor": ["Bus"],
    "SynMotor": ["Bus"],
    "CAP": ["Bus"],
    "StaticVar": ["Bus"],
    "Panel": ["Bus"],
}

# Symbol classification for the auto-layout Single Line Diagram view.
BRANCH_SYMBOL_KIND = {
    "XFMR2": "transformer", "XFMR3": "transformer",
    "HVCB": "breaker", "LVCB": "breaker", "Recloser": "breaker",
    "SPSTSwitch": "switch", "SPDTSwitch": "switch", "GroundSwitch": "switch",
    "Fuse": "switch", "Contactor": "switch",
    "Cable": "line", "BusDuct": "line", "XLine": "line", "OLH": "line", "Reactor": "line",
}
LEAF_SYMBOL_KIND = {
    "Utility": "source", "SynGen": "source",
    "WTGEN": "generator", "PVArray": "generator",
    "StaticLoad": "load", "Lump": "load", "IndMotor": "load", "SynMotor": "load", "Panel": "load",
    "CAP": "capacitor", "StaticVar": "capacitor",
}
# Priority order for picking the diagram's root bus(es): a bus with a Utility
# tie is the natural "top" of a radial one-line; fall back to SynGen, then
# to the highest-voltage bus if neither is present.
SOURCE_TABLES_PRIORITY = ["Utility", "SynGen"]

# Column names promoted to the front of the table for readability. Anything
# not listed keeps its original (schema) order after these.
PRIORITY_COLUMNS = [
    "ID", "Code", "Description", "InService", "Status",
    "Bus", "FromBus", "ToBus", "TertBus", "FromElement", "ToElement",
    "FaultedBus", "DeviceID", "PDID", "DeviceType", "PDType",
    "From", "To", "IDFrom", "IDTo", "IDBranch", "BusID", "LoadID", "IDTermBus",
    "NominalkV", "BasekV", "KV", "kVnom", "kVNom", "PrimkV", "SeckV", "RatedKV", "RatedkV",
    "MW", "Mvar", "MVAButton", "KW", "Kvar", "KVA", "MVA", "PF",
    "kASymm", "KASymm", "kASymm1", "kASymm3ph", "X/R",
    "VoltMag", "VoltMagA", "PercLoad", "Loading%A", "Loading%B", "Loading%C",
    "LengthValue", "CableLengthUnit",
]

NOISE_COLUMNS = {"IID", "Revision", "DataRevs", "Issue", "RevCtrl"}


def order_columns(all_columns):
    """Return column names reordered: priority columns first (in priority
    order, skipping ones absent from this table), then the rest as-is."""
    present = set(all_columns)
    front = [c for c in PRIORITY_COLUMNS if c in present]
    front_set = set(front)
    rest = [c for c in all_columns if c not in front_set]
    return front + rest


def categories_for_set(category_set: str):
    """Return the curated category dict for a project's category_set
    ("model", "sc_duty", "sc_fault", ...)."""
    if category_set == "model" or category_set not in STUDY_CATEGORIES:
        return CATEGORIES
    return STUDY_CATEGORIES[category_set]


def category_for_table(table_name, category_set: str = "model"):
    for key, cat in categories_for_set(category_set).items():
        if table_name in cat["tables"]:
            return key
    return None
