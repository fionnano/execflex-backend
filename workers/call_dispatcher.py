"""
Background worker/dispatcher for processing outbound call jobs.
Can be run as a separate process or scheduled task.

Usage:
    python -m workers.call_dispatcher
    # Or set up as cron: */1 * * * * (every minute)
"""
import os
import time
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.qualification_call_service import process_queued_jobs
from config.app_config import validate_config


def main():
    """Main dispatcher loop."""
    print("🚀 Starting outbound call job dispatcher...")
    
    # Validate config
    try:
        validate_config()
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)
    
    # Process jobs in a loop
    # For MVP: run once and exit (can be called by cron)
    # For production: could run in a loop with sleep
    limit = int(os.getenv("CALL_DISPATCHER_LIMIT", "10"))
    
    try:
        processed = process_queued_jobs(limit=limit)
        print(f"✅ Processed {processed} jobs")
        return processed
    except Exception as e:
        print(f"❌ Error processing jobs: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
