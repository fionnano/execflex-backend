"""
Background worker/dispatcher for processing outbound call jobs.

Designed to run continuously as a Render Background Worker.

Usage:
    # Local testing (runs once and exits)
    python -m workers.call_dispatcher
    
    # Render Background Worker (runs continuously)
    # Set Start Command: python -m workers.call_dispatcher --continuous
"""
import os
import time
import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.onboarding_service import process_queued_jobs
from config.app_config import validate_config


def main():
    """Main dispatcher loop."""
    parser = argparse.ArgumentParser(description='Process outbound call jobs')
    parser.add_argument(
        '--continuous',
        action='store_true',
        help='Run continuously (for Render Background Worker). Polls every 30 seconds.'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=30,
        help='Polling interval in seconds (default: 30)'
    )
    args = parser.parse_args()
    
    print("🚀 Starting outbound call job dispatcher...")
    if args.continuous:
        print(f"   Mode: Continuous (polling every {args.interval} seconds)")
    else:
        print("   Mode: Single run (exits after processing)")
    
    # Validate config
    try:
        validate_config()
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)
    
    limit = int(os.getenv("CALL_DISPATCHER_LIMIT", "10"))
    poll_interval = args.interval
    
    if args.continuous:
        # Continuous mode: run in a loop
        print(f"✅ Worker running continuously. Processing up to {limit} jobs every {poll_interval}s")
        print("   Press Ctrl+C to stop")
        
        try:
            while True:
                try:
                    processed = process_queued_jobs(limit=limit)
                    if processed > 0:
                        print(f"✅ Processed {processed} job(s)")
                    else:
                        print(f"ℹ️  No jobs to process (sleeping {poll_interval}s...)")
                    
                    time.sleep(poll_interval)
                except KeyboardInterrupt:
                    print("\n🛑 Worker stopped by user")
                    break
                except Exception as e:
                    print(f"❌ Error processing jobs: {e}")
                    import traceback
                    traceback.print_exc()
                    print(f"   Retrying in {poll_interval}s...")
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\n🛑 Worker stopped")
            sys.exit(0)
    else:
        # Single run mode: process once and exit
        try:
            processed = process_queued_jobs(limit=limit)
            print(f"✅ Processed {processed} job(s)")
            return processed
        except Exception as e:
            print(f"❌ Error processing jobs: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    main()
