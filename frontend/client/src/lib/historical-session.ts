export function readBatchIdFromLocation(search: string): number | null {
  const raw = new URLSearchParams(search).get("batch");
  if (!raw || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function historicalSessionPath(batchId: number): string {
  return `/historical?batch=${encodeURIComponent(String(batchId))}`;
}
