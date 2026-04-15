"""Initial Dealscout universe: 30 tickers from the Apr 7-10 2026 PDF + 15 manual adds."""

FROM_PDF = [
    "NBIS", "ALAB", "BE", "MRVL", "TER", "LRCX", "COHR", "AVGO", "CRDO", "STX",
    "CRWV", "WDC", "MKSI", "GLW", "AMD", "ETN", "CLS", "APH", "MU", "TSM",
    "TLN", "ARM", "FLNC", "FTNT", "NVTS",
    "SMCI", "LITE", "NVDA", "VRT", "PWR",
]

MANUAL_ADDS = [
    "ANET", "ASTS", "BWXT", "EME", "ETR", "LEU", "MOD", "MTZ", "NBT", "PL",
    "PRIM", "RKLB", "STRL", "TSEM", "TEN",
]

TICKERS = FROM_PDF + MANUAL_ADDS

# Authoritative ticker list now lives in the `universe` table.
# TICKERS is kept as a seed for first-run and as a fallback.
SEED_TICKERS = TICKERS

NAMES = {
    "NBIS": "Nebius Group", "ALAB": "Astera Labs", "BE": "Bloom Energy",
    "MRVL": "Marvell Technology", "TER": "Teradyne", "LRCX": "Lam Research",
    "COHR": "Coherent Corp", "AVGO": "Broadcom", "CRDO": "Credo Technology",
    "STX": "Seagate Technology", "CRWV": "CoreWeave", "WDC": "Western Digital",
    "MKSI": "MKS Instruments", "GLW": "Corning", "AMD": "Advanced Micro Devices",
    "ETN": "Eaton Corp", "CLS": "Celestica", "APH": "Amphenol",
    "MU": "Micron Technology", "TSM": "Taiwan Semiconductor",
    "TLN": "Talen Energy", "ARM": "Arm Holdings", "FLNC": "Fluence Energy",
    "FTNT": "Fortinet", "NVTS": "Navitas Semiconductor",
    "SMCI": "Super Micro Computer", "LITE": "Lumentum Holdings", "NVDA": "NVIDIA",
    "VRT": "Vertiv Holdings", "PWR": "Quanta Services",
    "ANET": "Arista Networks", "ASTS": "AST SpaceMobile", "BWXT": "BWX Technologies",
    "EME": "EMCOR Group", "ETR": "Entergy", "LEU": "Centrus Energy",
    "MOD": "Modine Manufacturing", "MTZ": "MasTec", "NBT": "NBT Bancorp",
    "PL": "Planet Labs", "PRIM": "Primoris Services", "RKLB": "Rocket Lab",
    "STRL": "Sterling Infrastructure", "TSEM": "Tower Semiconductor",
    "TEN": "Tsakos Energy Navigation",
}
