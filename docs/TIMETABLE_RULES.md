# Timetable Rules

## Default day configuration

- Monday-Friday are enabled for the prototype.
- Saturday is supported as a configurable working day but is not assumed to be active.
- Sunday is not a working day in the prototype.

## Fixed periods

| Slot | Start | End |
|---|---|---|
| S1 | 08:00 | 08:55 |
| S2 | 08:55 | 09:50 |
| S3 | 09:50 | 10:45 |
| S4 | 11:15 | 12:10 |
| S5 | 12:10 | 13:05 |
| S6 | 14:00 | 14:55 |
| S7 | 14:55 | 15:50 |
| S8 | 15:50 | 16:45 |
| S9 | 16:45 | 17:40 |

## System-defined breaks

- Morning break: 10:45-11:15 (30 minutes)
- Lunch: 13:05-14:00 (55 minutes)

Breaks are not teacher-entered schedule entries. They are fixed calendar periods.

## Schedule entry types

- `CLASS`: normal teaching period
- `LAB`: practical/lab activity that occupies one or more consecutive slots
- `OTHER`: other confirmed institutional activity that should count as occupied

`FREE` is derived from the absence of an occupied schedule entry for an enabled working slot.

## Lab rule

The prototype represents a lab as a logical activity with one or more consecutive slot IDs. A typical lab is two consecutive 55-minute slots (1 hour 50 minutes). The exact allowed duration must be validated against configured consecutive slots.

## Validation rules

- A schedule entry must use an enabled day and valid slot.
- A slot cannot contain two overlapping confirmed activities for the same teacher.
- A multi-slot lab must reference consecutive valid slots.
- Subject/activity details should be present for `CLASS`, `LAB`, and `OTHER` entries.
- Parser ambiguity must be flagged for review instead of silently converted to a guessed slot.
- Empty slots are interpreted as free only after validation of the timetable draft.

## Availability

For a day:

```text
enabled working slots
- occupied slots
= free slots
```

Break and lunch are displayed separately and are never reported as teacher free periods.
