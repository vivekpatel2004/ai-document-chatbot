const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Word-by-word reveal used when the backend can't stream yet.
 * Pushes ~2 words every 16ms so it feels fast but still "typed".
 */
export async function typewriter(
  text: string,
  onToken: (t: string) => void,
  signal: AbortSignal,
  delay = 16,
): Promise<void> {
  const parts = text.match(/\S+\s*|\s+/g) ?? [text];
  for (let i = 0; i < parts.length; i += 2) {
    if (signal.aborted) return;
    onToken(parts.slice(i, i + 2).join(''));
    await sleep(delay);
  }
}
