import { useCallback, useEffect, useRef, useState } from 'react'
import { speakText } from '../api'

const SPEAK_FAILED = "Couldn't read this answer aloud — the text is still here."

/**
 * Plays an advisor answer aloud through POST /advisor/speak.
 * `speak` resolves when playback ends, is stopped, or fails; it never throws.
 * Only one answer plays at a time: speaking again stops the previous one.
 */
export function useSpeaker() {
  const [speaking, setSpeaking] = useState(false)
  const [error, setError] = useState(null)
  const current = useRef(null) // { audio, url, resolve }
  const mounted = useRef(true)

  const stop = useCallback(() => {
    const playing = current.current
    current.current = null
    if (!playing) return
    playing.audio.onended = null
    playing.audio.onerror = null
    playing.audio.pause()
    URL.revokeObjectURL(playing.url)
    if (mounted.current) setSpeaking(false)
    playing.resolve()
  }, [])

  const speak = useCallback(
    async (text, links = []) => {
      stop()
      setError(null)
      setSpeaking(true)
      let blob
      try {
        blob = await speakText({ text, links })
      } catch {
        if (mounted.current) {
          setSpeaking(false)
          setError(SPEAK_FAILED)
        }
        return
      }
      if (!mounted.current) return
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      await new Promise((resolve) => {
        current.current = { audio, url, resolve }
        audio.onended = stop
        audio.onerror = stop
        Promise.resolve(audio.play()).catch(stop)
      })
    },
    [stop],
  )

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      stop()
    }
  }, [stop])

  return { speak, stop, speaking, error }
}
