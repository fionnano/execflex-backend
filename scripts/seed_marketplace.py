"""One-command marketplace demo seeder.

    python scripts/seed_marketplace.py          # seed (purges the namespace first)
    python scripts/seed_marketplace.py --purge   # purge only

Writes directly to Supabase via the service key in .env — does not require the
web app to be running. Idempotent: safe to run repeatedly.
"""
import os
import sys

# Ensure repo root on path when run as `python scripts/seed_marketplace.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    purge_only = "--purge" in sys.argv
    from services.marketplace import store
    if purge_only:
        counts = store.purge_marketplace()
        print(f"Purged marketplace namespace: {counts}")
        return
    from services.marketplace.seeder import seed
    result = seed(purge_first=True)
    print("Seeded ainm Marketplace:")
    print(f"  purged:        {result['purged']}")
    print(f"  leaders:       {result['leaders']}")
    print(f"  opportunities: {result['opportunities']}")
    print(f"  introductions: {result['introductions']}")


if __name__ == "__main__":
    main()
