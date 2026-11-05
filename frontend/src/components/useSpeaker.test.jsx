import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSpeaker } from './useSpeaker'
import { speakText } from '../api'

vi.mock('../api', () => ({ speakText: vi.fn() }))

let audios
class FakeAudio {
  constructor(src) {
    this.src = src
    this.pause = vi.fn()
    this.play = vi.fn(() => Promise.resolve())
    audios.push(this)
  }
}

beforeEach(() => {
  audios = []
  speakText.mockReset()
  globalThis.Audio = FakeAudio
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})

describe('useSpeaker', () => {
  it('is speaking until the audio ends', async () => {
    speakText.mockResolvedValue(new Blob(['mp3']))
    const { result } = renderHook(() => useSpeaker())
    let done
    await act(async () => {
      done = result.current.speak('Hello', [])
    })
    expect(result.current.speaking).toBe(true)
    expect(speakText).toHaveBeenCalledWith({ text: 'Hello', links: [] })
    await act(async () => {
      audios[0].onended()
      await done
    })
    expect(result.current.speaking).toBe(false)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x')
  })

  it('stop() pauses immediately', async () => {
    speakText.mockResolvedValue(new Blob(['mp3']))
    const { result } = renderHook(() => useSpeaker())
    let done
    await act(async () => {
      done = result.current.speak('Hello')
    })
    await act(async () => {
      result.current.stop()
      await done
    })
    expect(audios[0].pause).toHaveBeenCalled()
    expect(result.current.speaking).toBe(false)
  })

  it('a TTS failure sets error and does not throw', async () => {
    speakText.mockRejectedValue(Object.assign(new Error('no Deepgram key'), { status: 503 }))
    const { result } = renderHook(() => useSpeaker())
    await act(async () => {
      await result.current.speak('Hello')
    })
    expect(result.current.error).toMatch(/aloud/i)
    expect(result.current.speaking).toBe(false)
  })
})
