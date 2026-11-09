import { useCallback, useEffect, useRef, useState } from 'react'

interface UsePollingResult<T> {
  data: T | null
  error: string | null
  loading: boolean
  refresh: () => Promise<void>
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 10000,
  enabled: boolean = true,
): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const refresh = useCallback(async () => {
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Fetch failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs, enabled])

  return { data, error, loading, refresh }
}
