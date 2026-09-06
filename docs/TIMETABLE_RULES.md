# Timetable Rules

## Default day configuration

- Monday-Friday are enabled for the prototype.
- Saturday is supported as a configurable working day but is not assumed to be active.
- Sunday is not a working day in the prototype.

## Fixed teaching periods

| Slot | Start | End | Duration |
|---|---|---|---|
| S1 | 08:00 | 08:55 | 55 min |
| S2 | 08:55 | 09:50 | 55 min |
| S3 | 09:50 | 10:45 | 55 min |
| S4 | 11:15 | 12:10 | 55 min |
| S5 | 12:10 | 13:05 | 55 min |
| S6 | 14:00 | 14:55 | 55 min |
| S7 | 14:55 | 15:50 | 55 min |
| S8 | 15:50 | 16:45 | 55 min |
| S9 | 16:45 | 17:40 | 55 min |

## System-defined breaks

- Morning break: 10:45-11:15 (30 minutes)
- Lunch: 13:05-14:00 (55 minutes)

Breaks are not teacher-entered schedule entries. They are fixed calendar periods.

## Schedule entry types

- `CLASS`: normal teaching period
- `LAB`: practical/lab activity occupying multiple consecutive configured slots
- `OTHER`: other institutional activity that should count as occupied

`FREE` is derived from enabled working slots that have no occupied schedule entry.

## Lab rule

A lab is one logical schedule entry linked to multiple slot IDs. The standard department lab duration is 1 hour 50 minutes, which is exactly two consecutive 55-minute slots.

For the MVP, labs may only use valid consecutive slots. The frontend should make consecutive selection easy and the backend must validate the final payload.

Example:

```text
S6 + S7
14:00 - 15:50
```

must be represented as one lab entry, not two unrelated activities.

## Occupancy rule

A teacher cannot have two confirmed activities occupying the same day and configured time slot. This is validated both by the application service and by database-level deferred validation for confirmed timetables.

## Input rule

Teachers do not type arbitrary start/end times for normal timetable entries. They select from configured slots. This prevents timing drift and makes availability deterministic.

## Import rule

A parser must never silently invent a slot. If a source document cannot be mapped confidently to a configured day/slot, the result becomes a warning on the editable draft and must be resolved by the teacher before confirmation.

## Availability rule

For each enabled working day:

```text
enabled teaching slots
        -
occupied slots
        =
free slots
```

Break and lunch are returned/displayed separately and are never reported as teacher free periods.
