import { useCallback, useState } from 'react'

const STORAGE_KEY = 'eznas_advanced_mode'

export function useComplexityMode(): {
  isAdvanced: boolean
  toggleMode: () => void
} {
  const [isAdvanced, setIsAdvanced] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })

  const toggleMode = useCallback(() => {
    setIsAdvanced((prev) => {
      const next = !prev
      try {
        localStorage.setItem(STORAGE_KEY, String(next))
      } catch { /* ignore */ }
      return next
    })
  }, [])

  return { isAdvanced, toggleMode }
}
