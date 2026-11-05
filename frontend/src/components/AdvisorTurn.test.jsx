import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AdvisorTurn } from './AdvisorChat'

vi.mock('../api', () => ({}))

const reply = {
  answer: 'I cannot answer that.',
  source: 'engine',
  grounded: true,
  citations: [],
  links: [{ name: "the Registrar's Office", url: 'https://www.morgan.edu/registrar' }],
}

describe('AdvisorTurn', () => {
  it('renders curated links as safe anchors', () => {
    render(<AdvisorTurn reply={reply} />)
    const link = screen.getByRole('link', { name: "the Registrar's Office" })
    expect(link).toHaveAttribute('href', 'https://www.morgan.edu/registrar')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer')
  })

  it('offers read aloud when given onSpeak', async () => {
    const onSpeak = vi.fn()
    render(<AdvisorTurn reply={reply} onSpeak={onSpeak} />)
    await userEvent.click(screen.getByRole('button', { name: 'Read aloud' }))
    expect(onSpeak).toHaveBeenCalled()
  })
})
