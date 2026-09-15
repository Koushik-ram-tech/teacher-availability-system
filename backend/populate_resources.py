#!/usr/bin/env python3
"""
Populate resources table from existing timetable data.

This script scans schedule_entries.room values and:
1. Creates Resource records
2. Creates ResourceAlias records for common variations
3. Links schedule_entries to resources via resource_id

Run from backend directory:
    python populate_resources.py
"""
from app.db import SessionLocal
from app.services.resource_populator import (
    get_resource_inventory,
    populate_resources_from_timetable,
)


def main():
    print("=" * 70)
    print("Resource Population Script")
    print("=" * 70)
    print()

    db = SessionLocal()

    try:
        # Show current inventory
        print("Step 1: Scanning existing room data...")
        inventory = get_resource_inventory(db)

        print(f"\nFound {len(inventory)} unique room/resource references:\n")
        for room, count in inventory:
            print(f"  {room:15} - {count:3} schedule entries")

        print("\n" + "=" * 70)
        print("Step 2: Populating resources table...")
        print("=" * 70)
        print()

        # Populate resources (as shared resources, department=None)
        stats = populate_resources_from_timetable(db, department=None, commit=True)

        print("✓ Resource population complete!")
        print()
        print("Statistics:")
        print(f"  Rooms found:         {stats['rooms_found']}")
        print(f"  Resources created:   {stats['resources_created']}")
        print(f"  Resources reused:    {stats['resources_reused']}")
        print(f"  Aliases created:     {stats['aliases_created']}")
        print(f"  Entries linked:      {stats['entries_linked']}")
        print()
        print("Resources by type:")
        for rtype, count in sorted(stats['resources_by_type'].items()):
            print(f"  {rtype:15} - {count}")

        print()
        print("=" * 70)
        print("Done! Resources are now available for availability queries.")
        print("=" * 70)

    except Exception as exc:
        print(f"\n❌ Error: {exc}")
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
