import type { SVGProps } from 'react';

const base: SVGProps<SVGSVGElement> = {
  width: 20,
  height: 20,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

export const PaperclipIcon = () => (
  <svg {...base}><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
);
export const SendIcon = () => (
  <svg {...base}><path d="M22 2 11 13" /><path d="M22 2 15 22l-4-9-9-4 20-7z" /></svg>
);
export const StopIcon = () => (
  <svg {...base} fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
);
export const MenuIcon = () => (
  <svg {...base}><path d="M3 6h18M3 12h18M3 18h18" /></svg>
);
export const PlusIcon = () => (
  <svg {...base}><path d="M12 5v14M5 12h14" /></svg>
);
export const FileIcon = () => (
  <svg {...base}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></svg>
);
export const BotIcon = () => (
  <svg {...base}><rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 8V4M9 14h.01M15 14h.01" /></svg>
);
export const UserIcon = () => (
  <svg {...base}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
);
export const CloseIcon = () => (
  <svg {...base}><path d="M18 6 6 18M6 6l12 12" /></svg>
);
export const TrashIcon = () => (
  <svg {...base} width={16} height={16}><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" /></svg>
);
export const EditIcon = () => (
  <svg {...base} width={16} height={16}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
);
export const DownloadIcon = () => (
  <svg {...base} width={16} height={16}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" /></svg>
);
export const CopyIcon = () => (
  <svg {...base} width={16} height={16}><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
);
export const EraserIcon = () => (
  <svg {...base}><path d="M20 20H9l-5-5a2 2 0 0 1 0-2.8l9-9a2 2 0 0 1 2.8 0l5 5a2 2 0 0 1 0 2.8L12 20" /><path d="M7 11l6 6" /></svg>
);
export const SparkIcon = () => (
  <svg {...base} width={16} height={16}><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 17l.7 1.8L21.5 19.5l-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7z" /></svg>
);
export const ListIcon = () => (
  <svg {...base} width={16} height={16}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
);
export const CompareIcon = () => (
  <svg {...base} width={16} height={16}><rect x="3" y="4" width="7" height="16" rx="1.5" /><rect x="14" y="4" width="7" height="16" rx="1.5" /></svg>
);
