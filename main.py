"""
Polar data pipeline — run this daily after syncing your watch.

Steps:
  1. Fetch all new data from Polar API → SQLite
  2. Recompute CTL / ATL / TSB from training sessions
"""

from fetch_all import run as fetch
from compute_ctl_atl import run as compute

if __name__ == "__main__":
    print("=== Step 1: Fetch Polar data ===")
    fetch()

    print("\n=== Step 2: Compute CTL/ATL/TSB ===")
    compute()

    print("\n=== Done ===")