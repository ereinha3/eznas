import { useEffect, useRef, useState } from 'react'

export function useSSE(
  url: string,
  onEvent: (eventType: string, data: unknown) => void,
  enabled: boolean = true,
): { connected: boolean } {
  const [connected, setConnected] = useState(false)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    if (!enabled) return

    const token = localStorage.getItem('nas_orchestrator_token')
    const separator = url.includes('?') ? '&' : '?'
    const fullUrl = token ? `${url}${separator}token=${encodeURIComponent(token)}` : url

    const es = new EventSource(fullUrl)

    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)

    // Listen for all named events
    const handler = (evt: MessageEvent) => {
      try {
        const data = JSON.parse(evt.data)
        onEventRef.current(evt.type, data)
      } catch { /* ignore parse errors */ }
    }

    // Register for known event types
    es.addEventListener('pipeline_event', handler)
    es.addEventListener('stage', handler)
    es.addEventListener('status', handler)

    return () => {
      es.close()
      setConnected(false)
    }
  }, [url, enabled])

  return { connected }
}
