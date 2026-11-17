import { SparkIcon } from './Icons';

export default function SuggestionChips({ items, onPick }: { items: string[]; onPick: (q: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="suggestions">
      {items.map((q) => (
        <button key={q} className="suggestion" onClick={() => onPick(q)}>
          <SparkIcon /> {q}
        </button>
      ))}
    </div>
  );
}
