export function formatDateTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 19);
}
