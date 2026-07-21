"""
Shared GDELT organization-matching logic — the finalized rules from
single-day testing (word-boundary matching, generic-fragment blocklist,
ticker-specific exclusions, tier-gated short aliases).
"""

import csv
import re
from pathlib import Path

FINANCIAL_THEME_PREFIXES = ["ECON_", "BUS_", "MARKET", "STOCK"]

TIER2_THEME_GATED = {
    "ACC.NS", "DLF.NS", "SRF.NS", "RECLTD.NS", "CDW", "CSX", "TRI",
}

LONG_NAME_ONLY = {
    "MSTR", "HAL.NS", "ITC.NS", "TRENT.NS",
}

GENERIC_ORG_BLOCKLIST = {
    "health care", "healthcare", "management", "group", "services",
    "government", "committee", "council", "department", "ministry",
    "association", "foundation", "institute", "agency", "authority",
    "board", "commission", "office", "bureau", "center", "centre",
    "holdings", "partners", "capital", "solutions", "systems",
    "international", "national", "global", "industries", "enterprises",
    "corporation", "company", "limited", "incorporated",
    "holdings inc", "systems inc", "technologies inc", "power company",
    "energy group", "energy corporation", "energy solutions",
    "communications inc", "economic zone limited", "motor company",
    "us inc",
}

TICKER_SPECIFIC_EXCLUSIONS = {
    "TRENT.NS": {"severn trent", "nottingham trent"},
    "ACC.NS": {"henderson", "i acc"},
}


def normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def load_aliases(alias_table_path: Path):
    """Returns list of (ticker, alias_text, is_short_alias) tuples."""
    aliases = []
    with open(alias_table_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ticker = row["ticker"]
            if row.get("long_name"):
                aliases.append((ticker, normalize(row["long_name"]), False))
            if row.get("short_alias") and ticker not in LONG_NAME_ONLY:
                aliases.append((ticker, normalize(row["short_alias"]), True))
    return aliases


def has_financial_theme(themes_field: str) -> bool:
    if not themes_field:
        return False
    themes = themes_field.upper()
    return any(prefix in themes for prefix in FINANCIAL_THEME_PREFIXES)


def word_boundary_match(alias: str, org: str) -> bool:
    shorter, longer = (alias, org) if len(alias) <= len(org) else (org, alias)
    if not shorter:
        return False
    pattern = r"\b" + re.escape(shorter) + r"\b"
    return re.search(pattern, longer) is not None


def match_organizations(org_field: str, themes_field: str, aliases: list) -> list:
    """Returns deduped list of (ticker, matched_org_string) for this row."""
    if not org_field:
        return []

    orgs = [normalize(o) for o in org_field.split(";") if o.strip()]
    financial_ok = has_financial_theme(themes_field)

    matches = set()
    for org in orgs:
        if not org or org in GENERIC_ORG_BLOCKLIST:
            continue
        for ticker, alias, is_short in aliases:
            if not alias:
                continue
            if ticker in TIER2_THEME_GATED and is_short and not financial_ok:
                continue
            excluded = TICKER_SPECIFIC_EXCLUSIONS.get(ticker, set())
            if any(bad in org for bad in excluded):
                continue
            if word_boundary_match(alias, org):
                matches.add((ticker, org))
    return list(matches)