/** Format HttpClient / API error bodies for snack messages. */
export function formatApiError(err: unknown): string {
  if (err && typeof err === 'object' && 'error' in err) {
    return JSON.stringify((err as { error: unknown }).error);
  }
  return String(err);
}
