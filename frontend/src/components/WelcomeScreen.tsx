import { FileIcon } from './Icons';
import SuggestionChips from './SuggestionChips';

interface Props {
  docCount: number;
  suggestions: string[];
  onPick: () => void;
  onAsk: (q: string) => void;
}

export default function WelcomeScreen({ docCount, suggestions, onPick, onAsk }: Props) {
  return (
    <div className="welcome">
      <h1>{docCount ? `Ask about your ${docCount === 1 ? 'document' : `${docCount} documents`}` : 'Ask your documents anything'}</h1>
      <p>
        {docCount
          ? 'Answers include the document name and page number. Use the tools in the sidebar for summaries, extraction and comparison.'
          : 'Upload one or more PDF, Word or text files, then ask questions across all of them.'}
      </p>
      <button className="btn-primary large" onClick={onPick}>
        <FileIcon /> {docCount ? 'Add more documents' : 'Upload documents'}
      </button>
      <SuggestionChips items={suggestions} onPick={onAsk} />
      <p className="muted small">Supports PDF, DOCX and TXT</p>
    </div>
  );
}
