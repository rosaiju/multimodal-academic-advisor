import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvisorTurn, provenance } from './AdvisorChat'

vi.mock('../api', () => ({}))

const base = { answer: 'You still need 97.00 credits.', grounded: true, citations: [] }

describe('provenance label', () => {
  it('says Gemini wrote it when the API says a model answer was used', () => {
    const reply = { ...base, source: 'engine+llm', provider: 'gemini', model: 'gemini-2.5-flash' }
    render(<AdvisorTurn reply={reply} />)
    expect(screen.getByText('Written by Gemini · checked by the engine')).toBeInTheDocument()
  })

  it("says it is a fallback when Gemini's answer failed the fact check", () => {
    const p = provenance({ ...base, source: 'engine', provider: 'gemini', fallback: 'check_failed' })
    expect(p.kind).toBe('fallback')
    expect(p.label).toBe("Fallback · Gemini's answer failed the fact check")
  })

  it('says it is a fallback when Gemini could not answer', () => {
    for (const fallback of ['model_unavailable', 'model_error']) {
      const p = provenance({ ...base, source: 'engine', provider: 'gemini', fallback })
      expect(p.label).toBe('Fallback · Gemini unavailable')
    }
  })

  it('says the engine wrote it when no model was asked to', () => {
    for (const fallback of [undefined, null, 'rephrase_off']) {
      const p = provenance({ ...base, source: 'engine', fallback })
      expect(p).toMatchObject({ kind: 'engine', label: 'Computed by the engine' })
    }
  })
})
