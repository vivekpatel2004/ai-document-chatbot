import type { Source } from '../types';

export default function SourceList({ sources, content }: { sources: Source[]; content: string }) {
  if (!sources.length) return null;
  const seen = new Set<string>();
  const unique = sources.filter((source) => {
    const key = `${source.document_id || source.document}:${source.page ?? 'unknown'}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const citationMarkers = new Set(Array.from(content.matchAll(/\[(\d+)\]/g), (match) => Number(match[1])));
  const cited = unique.filter((source) => source.n != null && citationMarkers.has(source.n));
  const shown = cited.length ? cited : unique;
  return (
    <div className="sources">
      <span className="sources-title">Sources</span>
      <div className="sources-row">
        {shown.map((s) => (
          <span key={s.n} className="source-chip" title={s.snippet}>
            <b>[{s.n}]</b> {s.document}
            {s.page != null && s.page > 0 ? ` · p. ${s.page}` : ''}
          </span>
        ))}
      </div>
    </div>
  );
}
