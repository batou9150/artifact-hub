const s = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }

export const Icon = {
  lock: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.2" {...s} /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" {...s} /></svg>,
  people: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><circle cx="6" cy="5.5" r="2.3" {...s} /><path d="M1.8 13.5c.5-2.4 2.2-3.6 4.2-3.6s3.7 1.2 4.2 3.6M10.5 3.4a2.2 2.2 0 010 4.3M12 9.9c1.2.4 2 1.6 2.3 3.6" {...s} /></svg>,
  globe: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" {...s} /><path d="M2 8h12M8 2c1.8 1.7 2.6 3.7 2.6 6S9.8 12.3 8 14c-1.8-1.7-2.6-3.7-2.6-6S6.2 3.7 8 2z" {...s} /></svg>,
  link: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5l3-3M7 5l1-1a2.1 2.1 0 013 3l-1 1M9 11l-1 1a2.1 2.1 0 01-3-3l1-1" {...s} /></svg>,
  clock: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" {...s} /><path d="M8 5v3.2L10 10" {...s} /></svg>,
  pencil: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><path d="M11 2.5l2.5 2.5L6 12.5l-3 .5.5-3L11 2.5z" {...s} /></svg>,
  trash: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8A1 1 0 006 13.5h4a1 1 0 001-.9l.6-8" {...s} /></svg>,
  eye: <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" {...s} /><circle cx="8" cy="8" r="1.8" {...s} /></svg>,
  search: <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden="true"><circle cx="8" cy="8" r="5" {...s} strokeWidth={1.6} /><path d="M12 12l3 3" {...s} strokeWidth={1.6} /></svg>,
  plus: <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" {...s} strokeWidth={1.8} /></svg>,
  dots: <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><circle cx="4" cy="9" r="1.4" fill="currentColor" /><circle cx="9" cy="9" r="1.4" fill="currentColor" /><circle cx="14" cy="9" r="1.4" fill="currentColor" /></svg>,
  back: <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3L5 8l5 5" {...s} strokeWidth={1.6} /></svg>,
  code: <svg width="30" height="30" viewBox="0 0 34 34" aria-hidden="true"><path d="M13 11l-6 6 6 6M21 11l6 6-6 6" {...s} strokeWidth={1.8} /></svg>,
  md: <svg width="30" height="30" viewBox="0 0 34 34" aria-hidden="true"><rect x="6" y="9" width="22" height="16" rx="2.5" {...s} strokeWidth={1.8} /><path d="M10 21v-8l3.5 4 3.5-4v8M23 13v8m0 0l-2.5-2.8M23 21l2.5-2.8" {...s} strokeWidth={1.6} /></svg>,
  text: <svg width="30" height="30" viewBox="0 0 34 34" aria-hidden="true"><path d="M9 11h16M9 16h16M9 21h10" {...s} strokeWidth={1.8} /></svg>,
}

export function KindIcon({ kind }: { kind: 'html' | 'markdown' | 'text' }) {
  return kind === 'html' ? Icon.code : kind === 'markdown' ? Icon.md : Icon.text
}
