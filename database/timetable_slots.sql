-- Default department timetable slot seed.
-- Keep slot IDs stable because schedule entries reference them.

INSERT INTO time_slots (code, start_time, end_time, sequence, is_active)
VALUES
  ('S1', '08:00', '08:55', 1, TRUE),
  ('S2', '08:55', '09:50', 2, TRUE),
  ('S3', '09:50', '10:45', 3, TRUE),
  ('S4', '11:15', '12:10', 4, TRUE),
  ('S5', '12:10', '13:05', 5, TRUE),
  ('S6', '14:00', '14:55', 6, TRUE),
  ('S7', '14:55', '15:50', 7, TRUE),
  ('S8', '15:50', '16:45', 8, TRUE),
  ('S9', '16:45', '17:40', 9, TRUE)
ON CONFLICT (code) DO NOTHING;

-- Fixed breaks are represented as configuration, not teacher schedule entries.
-- Morning break: 10:45-11:15
-- Lunch: 13:05-14:00
