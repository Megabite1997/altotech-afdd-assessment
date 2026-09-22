import { useCallback, useEffect, useRef, useState } from 'react'

export type AsyncResult<T> = {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

/** Fetch with explicit loading/error state and optional polling. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], pollMs = 0): AsyncResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const alive = useRef(true)
  const fnRef = useRef(fn)
  fnRef.current = fn

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    alive.current = true
    let timer: number | undefined

    const run = async (showSpinner: boolean) => {
      if (showSpinner) setLoading(true)
      try {
        const result = await fnRef.current()
        if (!alive.current) return
        setData(result)
        setError(null)
      } catch (err) {
        if (!alive.current) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (alive.current) setLoading(false)
      }
      if (alive.current && pollMs > 0) timer = window.setTimeout(() => run(false), pollMs)
    }

    run(true)
    return () => {
      alive.current = false
      if (timer) window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, pollMs])

  return { data, loading, error, reload }
}
