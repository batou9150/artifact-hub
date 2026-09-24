import { useRef } from 'react'

// The ONLY place untrusted artifact content is displayed.
//
// sandbox="allow-scripts" and nothing else: the artifact's own JavaScript runs,
// but in an opaque origin (no access to this app's storage, cookies or tokens),
// with no popups, no top navigation, no forms. NEVER add allow-same-origin: with
// allow-scripts it lets the frame remove its own sandbox. The server repeats the
// same restriction with `Content-Security-Policy: sandbox allow-scripts` and
// blocks all network egress.
export const FRAME_SANDBOX = 'allow-scripts'

export function ArtifactFrame({ src, title, onNavigatedAway }: {
  src: string
  title: string
  onNavigatedAway?: () => void
}) {
  // Browsers enforce no CSP directive for frame self-navigation, so an artifact's
  // script can still navigate its own frame elsewhere. Detect a second load and
  // tell the viewer instead of silently showing foreign content.
  const loads = useRef(0)
  return (
    <iframe
      key={src}
      className="artifact-frame"
      title={title}
      src={src}
      sandbox={FRAME_SANDBOX}
      referrerPolicy="no-referrer"
      allow=""
      onLoad={() => {
        loads.current += 1
        if (loads.current > 1) onNavigatedAway?.()
      }}
    />
  )
}
