import { FALLBACK_PERIODS } from './types';

export function formatTime(value: string): string {
  const [hourText, minuteText] = value.split(':');
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isInteger(minute) || minute < 0 || minute > 59) {
    return value;
  }

  const suffix = hour < 12 ? 'AM' : 'PM';
  const hour12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
  return `${hour12}:${String(minute).padStart(2, '0')} ${suffix}`;
}

export function formatTimeRange(start: string, end: string): string {
  return `${formatTime(start)} – ${formatTime(end)}`;
}

export function formatSlotCodesRange(slotCodes: string[]): string {
  const scheduleSlots = FALLBACK_PERIODS.filter((period) => period.kind === 'SLOT' && period.code);
  const selected = slotCodes
    .map((code) => scheduleSlots.find((period) => period.code === code))
    .filter((period): period is (typeof scheduleSlots)[number] => Boolean(period));

  if (selected.length === 0) return slotCodes.join(' + ');
  return formatTimeRange(selected[0].start_time, selected[selected.length - 1].end_time);
}