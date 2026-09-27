import type { DOCXResolvedActivity, DOCXUnresolvedBlock, ResourceCatalogItem } from './types';

export function getAvailableResourceCandidates(
  catalog: ResourceCatalogItem[],
  resolvedActivities: DOCXResolvedActivity[],
  block: DOCXUnresolvedBlock,
): ResourceCatalogItem[] {
  const requestedSlots = new Set(block.slots);
  const conflictingCodes = new Set<string>();

  for (const activity of resolvedActivities) {
    if (activity.day !== block.day || !activity.slots.some((slot) => requestedSlots.has(slot))) continue;
    const linkedCodes = activity.resource_codes.length > 0
      ? activity.resource_codes
      : (activity.resource_code ?? '').split(',').map((code) => code.trim()).filter(Boolean);
    for (const code of linkedCodes) conflictingCodes.add(code.trim().toLowerCase());
  }

  return catalog.filter((resource) => !conflictingCodes.has(resource.code.trim().toLowerCase()));
}