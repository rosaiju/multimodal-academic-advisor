import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useVoiceConversation } from './useVoiceConversation'
import { askAdvisor } from '../api'
import { useLiveTranscription } from './useLiveTranscription'
import { useSpeaker } from './useSpeaker'

vi.mock('../api', () => ({ askAdvisor: vi.fn() }))
vi.mock('./useLiveTranscription', () => ({ useLiveTranscription: vi.fn() }))
vi.mock('./useSpeaker', () => ({ useSpeaker: vi.fn() }))

let live, speaker, finishSpeaking

function reply(answer, extra = {}) {
  return { answer, source: 'engine', grounded: true, citations: [], links: [], ...extra }
}

beforeEach(() => {
  askAdvisor.mockReset()
  live = { supported: true, listening: false, liveText: '', error: null, start: vi.fn(), stop: vi.fn() }
  useLiveTranscription.mockImplementation((opts) => {
    live.onDone = opts.onDone
    return live
  })
  speaker = {
    speaking: false,
    error: null,
    stop: vi.fn(() => finishSpeaking?.()),
    speak: vi.fn(
      () =>
        new Promise((resolve) => {
          finishSpeaking = resolve
        }),
    ),
  }
  useSpeaker.mockImplementation(() => speaker)
})

async function say(text, confidence = 0.95) {
  await act(async () => {
    live.onDone({ text, confidence })
  })
}

async function endSpeech() {
  await act(async () => {
    finishSpeaking()
  })
}

describe('useVoiceConversation', () => {
  it('push-to-talk: one turn goes listening -> thinking -> speaking -> idle', async () => {
    askAdvisor.mockResolvedValue(reply('You have 7 courses left.'))
    const { result } = renderHook(() => useVoiceConversation({ handsFree: false }))
    act(() => result.current.start())
    expect(result.current.state).toBe('listening')
    expect(live.start).toHaveBeenCalledTimes(1)
    await say('what do I have left')
    expect(askAdvisor).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'what do I have left', mode: 'live', confidence: 0.95 }),
    )
    expect(result.current.state).toBe('speaking')
    expect(speaker.speak).toHaveBeenCalledWith('You have 7 courses left.', [])
    await endSpeech()
    expect(result.current.state).toBe('idle')
    expect(result.current.turns.map((t) => t.role)).toEqual(['user', 'advisor'])
  })

  it('hands-free returns to listening after speaking', async () => {
    askAdvisor.mockResolvedValue(reply('ok'))
    const { result } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('hi there')
    await endSpeech()
    expect(result.current.state).toBe('listening')
  })

  it('an empty utterance asks nothing and keeps listening', async () => {
    const { result } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('   ', 0)
    expect(askAdvisor).not.toHaveBeenCalled()
    expect(result.current.state).toBe('listening')
    expect(live.start).toHaveBeenCalledTimes(2)
  })

  it('a speech failure still shows the text and goes back to listening', async () => {
    askAdvisor.mockResolvedValue(reply('ok'))
    speaker.speak = vi.fn(() => Promise.resolve())
    const { result } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('hi there')
    expect(result.current.turns.at(-1).reply.answer).toBe('ok')
    expect(result.current.state).toBe('listening')
  })

  it('"yeah" after a read-back re-asks the original question with the confirmed code', async () => {
    askAdvisor.mockResolvedValueOnce(
      reply('Did you mean COSC 241?', {
        source: 'readback',
        clarification: { spoken: 'COSC 241', candidates: ['COSC 241'], question: 'Did you mean COSC 241?' },
      }),
    )
    askAdvisor.mockResolvedValueOnce(reply('For COSC 241: you can take it.'))
    const { result } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('can I take COSC 241', 0.4)
    await endSpeech()
    await say('yeah', 0.9)
    expect(askAdvisor).toHaveBeenLastCalledWith(
      expect.objectContaining({ message: 'can I take COSC 241', confidence: 1 }),
    )
  })

  it('barge-in: real words while speaking stop playback and listen', async () => {
    askAdvisor.mockResolvedValue(reply('A long answer...'))
    const { result, rerender } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('what do I have left')
    expect(result.current.state).toBe('speaking')
    live.liveText = 'wait'
    await act(async () => rerender())
    expect(speaker.stop).toHaveBeenCalled()
    expect(result.current.state).toBe('listening')
  })

  it('a stray sound while speaking does not interrupt', async () => {
    askAdvisor.mockResolvedValue(reply('A long answer...'))
    const { result, rerender } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('what do I have left')
    live.liveText = 'a'
    await act(async () => rerender())
    expect(speaker.stop).not.toHaveBeenCalled()
    expect(result.current.state).toBe('speaking')
  })

  it('a stale reply arriving after the session ends is dropped', async () => {
    let answer
    askAdvisor.mockReturnValue(new Promise((r) => (answer = r)))
    const { result } = renderHook(() => useVoiceConversation({ handsFree: true }))
    act(() => result.current.start())
    await say('what do I have left')
    act(() => result.current.end())
    await act(async () => answer(reply('late')))
    expect(speaker.speak).not.toHaveBeenCalled()
    expect(result.current.turns.map((t) => t.role)).toEqual(['user'])
    expect(result.current.state).toBe('idle')
  })
})
