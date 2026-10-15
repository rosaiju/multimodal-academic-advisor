import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReviewStep from './ReviewStep'
import { confirmCourses } from '../api'

vi.mock('../api', () => ({ confirmCourses: vi.fn() }))

const PLACEHOLDER_REASON =
  'This is a summary line, not a course - it has no grade and no credit hours, and the ' +
  'credit it totals is listed separately below. It cannot be added to your record.'

function course(code, overrides = {}) {
  return {
    code,
    title: null,
    institution: null,
    term: 'Fall 2023',
    grade: 'A',
    credits: 3,
    confidence: 'high',
    provenance: 'unconfirmed',
    is_placeholder: false,
    blocking_reason: null,
    issues: [],
    ...overrides,
  }
}

// Mirrors what the backend sends for the demo transcript's transfer heading:
// "ORTR 101 TRANSFER OF 24 CREDITS  TR  0  FALL 2023".
const placeholder = course('ORTR101', {
  title: 'TRANSFER OF 24 CREDITS',
  grade: null,
  credits: 0,
  is_placeholder: true,
  blocking_reason: PLACEHOLDER_REASON,
})

function extractionWith(courses) {
  return {
    summary: `${courses.length} rows read`,
    extraction: {
      courses,
      warnings: [],
      source_name: 'demo_transcript.txt',
      extractor: 'text',
    },
  }
}

function renderReview(courses) {
  const onConfirmed = vi.fn()
  render(
    <ReviewStep
      extraction={extractionWith(courses)}
      programId="morgan-cs-bs"
      onConfirmed={onConfirmed}
      onBack={vi.fn()}
    />,
  )
  return { onConfirmed }
}

const confirmButton = () => screen.getByRole('button', { name: /^Confirm \d+ course/ })
const rowFor = (code) => within(screen.getByRole('table')).getByText(code).closest('tr')
const checkboxFor = (code) => within(rowFor(code)).getByRole('checkbox')

beforeEach(() => {
  confirmCourses.mockReset()
  confirmCourses.mockResolvedValue({ stored: true })
})

describe('ReviewStep', () => {
  it('starts with every clean row selected', () => {
    renderReview([course('COSC111'), course('COSC112'), course('MATH241')])

    for (const code of ['COSC111', 'COSC112', 'MATH241']) {
      expect(checkboxFor(code)).toBeChecked()
    }
    expect(confirmButton()).toHaveTextContent('Confirm 3 courses')
    expect(confirmButton()).toBeEnabled()
  })

  it('updates the count and button text when a row is unticked', async () => {
    const user = userEvent.setup()
    renderReview([course('COSC111'), course('COSC112'), course('MATH241')])

    await user.click(checkboxFor('COSC112'))

    expect(checkboxFor('COSC112')).not.toBeChecked()
    expect(confirmButton()).toHaveTextContent('Confirm 2 courses')

    await user.click(checkboxFor('MATH241'))
    expect(confirmButton()).toHaveTextContent(/^Confirm 1 course$/)
  })

  it('never lets a summary line such as ORTR 101 be confirmed', async () => {
    const user = userEvent.setup()
    renderReview([course('COSC111'), placeholder, course('MATH241')])

    const box = checkboxFor('ORTR101')
    expect(box).not.toBeChecked()
    expect(box).toBeDisabled()
    expect(within(rowFor('ORTR101')).getByText(PLACEHOLDER_REASON)).toBeInTheDocument()
    expect(confirmButton()).toHaveTextContent('Confirm 2 courses')

    // Neither clicking the box nor "Select every confirmable row" ticks it.
    await user.click(box)
    expect(box).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Select every confirmable row' }))
    expect(box).not.toBeChecked()
    expect(confirmButton()).toHaveTextContent('Confirm 2 courses')

    await user.click(confirmButton())
    const sent = confirmCourses.mock.calls[0][0].items.map((item) => item.extracted.code)
    expect(sent).toEqual(['COSC111', 'MATH241'])
  })

  it('sends nothing to the backend until Confirm is clicked', async () => {
    const user = userEvent.setup()
    const { onConfirmed } = renderReview([course('COSC111'), course('COSC112')])

    // Unticking, re-ticking and editing a field all stay local.
    await user.click(checkboxFor('COSC112'))
    await user.click(checkboxFor('COSC112'))
    const grade = within(rowFor('COSC111')).getByDisplayValue('A')
    await user.clear(grade)
    await user.type(grade, 'B')
    expect(confirmCourses).not.toHaveBeenCalled()
    expect(onConfirmed).not.toHaveBeenCalled()

    await user.click(confirmButton())

    expect(confirmCourses).toHaveBeenCalledTimes(1)
    expect(confirmCourses).toHaveBeenCalledWith({
      programId: 'morgan-cs-bs',
      sourceName: 'demo_transcript.txt',
      extractor: 'text',
      items: [
        // Only the field the student actually changed travels beside the original.
        { extracted: course('COSC111'), grade: 'B' },
        { extracted: course('COSC112') },
      ],
    })
    expect(onConfirmed).toHaveBeenCalledTimes(1)
  })

  it('blocks Confirm while a ticked row is missing a field', async () => {
    const user = userEvent.setup()
    renderReview([course('COSC111'), course('COSC112')])

    await user.clear(within(rowFor('COSC112')).getByDisplayValue('A'))

    expect(confirmButton()).toBeDisabled()
    expect(screen.getByText('1 ticked row cannot be confirmed yet')).toBeInTheDocument()

    await user.click(checkboxFor('COSC112'))
    expect(confirmButton()).toBeEnabled()
    expect(confirmButton()).toHaveTextContent(/^Confirm 1 course$/)
  })

  it('shows the error and stays on the screen when saving fails', async () => {
    const user = userEvent.setup()
    confirmCourses.mockRejectedValue(new Error('Cannot reach the backend.'))
    const { onConfirmed } = renderReview([course('COSC111')])

    await user.click(confirmButton())

    expect(await screen.findByText('Cannot reach the backend.')).toBeInTheDocument()
    expect(screen.getByText('Could not save')).toBeInTheDocument()
    expect(onConfirmed).not.toHaveBeenCalled()
  })
})
