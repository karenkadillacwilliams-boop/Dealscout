"""30 fixture headlines for the keyword scorer golden-file suite.
Each tuple: (headline, form_type, expected_min_kw_score, expected_tag)."""

CONFIRMED_MA = [
    ("ACME Corp to acquire Widgets Inc for $2.1B in cash", None, 35, "m&a-confirmed"),
    ("ACME and Widgets enter definitive agreement on merger", None, 35, "m&a-confirmed"),
    ("Widgets announces merger agreement with ACME Corp", None, 35, "m&a-confirmed"),
    ("ACME agrees to acquire rival Pinnacle in stock deal", None, 35, "m&a-confirmed"),
    ("Tender offer launched by Alpha for all outstanding Beta shares", None, 35, "m&a-confirmed"),
    ("Beta Corp files 8-K: entry into merger agreement", "8-K", 55, "m&a-confirmed"),
    ("Alpha Industries to acquire Beta for $800M", None, 35, "m&a-confirmed"),
    ("Definitive agreement reached between Delta and Epsilon", None, 35, "m&a-confirmed"),
    ("Gamma Corp launches tender offer for Omega", None, 35, "m&a-confirmed"),
    ("Zeta agrees to acquire Theta Systems", None, 35, "m&a-confirmed"),
]

RUMORED_MA = [
    ("Sources: ACME in talks to acquire Widgets", None, 25, "m&a-rumor"),
    ("Beta Corp exploring sale, sources say", None, 25, "m&a-rumor"),
    ("Gamma exploring strategic alternatives including sale", None, 25, "m&a-rumor"),
    ("Report: Alpha weighing bid for Omega", None, 25, "m&a-rumor"),
    ("Delta approached about takeover, per Bloomberg", None, 25, "m&a-rumor"),
    ("Epsilon considering offer from private equity group", None, 25, "m&a-rumor"),
    ("Theta in talks to acquire smaller rival", None, 25, "m&a-rumor"),
    ("Zeta exploring sale of semiconductor unit", None, 25, "m&a-rumor"),
    ("Pinnacle approached about potential acquisition", None, 25, "m&a-rumor"),
    ("Sources: Omega exploring strategic alternatives", None, 25, "m&a-rumor"),
]

NOISE = [
    ("Q3 earnings beat analyst estimates by 2 cents", None, 0, None),
    ("Company announces quarterly dividend of $0.25", None, 0, None),
    ("CEO to present at investor conference next week", None, 0, None),
    ("Firm denies rumor of any acquisition talks", None, 0, "weak"),
    ("ACME says speculation only, not in talks", None, 0, "weak"),
    ("Board approves $500M buyback program", None, 0, None),
    ("Company reports 5% revenue growth year-over-year", None, 0, None),
    ("Analyst upgrades stock to buy", None, 0, None),
    ("Firm files routine 10-K annual report", "10-K", 0, None),
    ("Stock price rises on broad market rally", None, 0, None),
]

ALL = CONFIRMED_MA + RUMORED_MA + NOISE
