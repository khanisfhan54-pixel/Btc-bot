import pandas as pd
import os

# =========================
# CONFIG
# =========================
DATA_PATH = "data"
TRADES_FILE = os.path.join(DATA_PATH, "aggTrades.csv")
DEPTH_FILE = os.path.join(DATA_PATH, "bookDepth.csv")

OUTPUT_TRADES = os.path.join(DATA_PATH, "aggTrades_clean.csv")
OUTPUT_DEPTH = os.path.join(DATA_PATH, "bookDepth_clean.csv")

# =========================
# LOAD DATA
# =========================
print("Loading data...")

df_trades = pd.read_csv(TRADES_FILE)
df_depth = pd.read_csv(DEPTH_FILE)

# =========================
# FIX TIMESTAMP (CRITICAL)
# =========================
print("Fixing timestamps...")

# aggTrades already in ms → just rename for consistency
df_trades = df_trades.rename(columns={"transact_time": "timestamp"})

# convert bookDepth timestamp → milliseconds
df_depth["timestamp"] = pd.to_datetime(df_depth["timestamp"], errors="coerce")
df_depth = df_depth.dropna(subset=["timestamp"])

df_depth["timestamp"] = df_depth["timestamp"].astype("int64") // 10**6

# =========================
# SORT DATA (MANDATORY)
# =========================
print("Sorting data...")

df_trades = df_trades.sort_values("timestamp").reset_index(drop=True)
df_depth = df_depth.sort_values("timestamp").reset_index(drop=True)

# =========================
# VALIDATION CHECKS
# =========================
print("Running validation...")

print("\nTrades Time Range:")
print(df_trades["timestamp"].min(), "→", df_trades["timestamp"].max())

print("\nDepth Time Range:")
print(df_depth["timestamp"].min(), "→", df_depth["timestamp"].max())

# Check overlap
overlap_start = max(df_trades["timestamp"].min(), df_depth["timestamp"].min())
overlap_end = min(df_trades["timestamp"].max(), df_depth["timestamp"].max())

if overlap_start >= overlap_end:
    print("\n❌ ERROR: No overlapping time range!")
else:
    print("\n✅ Overlap OK:")
    print(overlap_start, "→", overlap_end)

# =========================
# BASIC DATA QUALITY CHECKS
# =========================
print("\nChecking data quality...")

print("Trades missing:", df_trades.isnull().sum().sum())
print("Depth missing:", df_depth.isnull().sum().sum())

# =========================
# SAVE CLEAN DATA
# =========================
print("\nSaving cleaned data...")

df_trades.to_csv(OUTPUT_TRADES, index=False)
df_depth.to_csv(OUTPUT_DEPTH, index=False)

print("\n✅ DONE — Data is now aligned and clean.")
