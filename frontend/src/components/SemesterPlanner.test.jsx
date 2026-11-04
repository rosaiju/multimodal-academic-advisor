import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SemesterPlanner, { FALLBACK_CAVEAT } from './SemesterPlanner'
import { generateSchedule, validateSchedule } from '../api'

vi.mock('../api', () => ({ generateSchedule: vi.fn(), validateSchedule: vi.fn() }))

const course = (code, credits = '3') => ({
  course: { code, title: `${code} title`, credits },
  reasons: [`required by ${code}`],
  warnings: [],
})

const PLAN = {
  terms: [
    { term: 'Spring 2027', courses: [course('COSC220', '4')] },
    { term: 'Fall 2027', courses: [course('COSC352')] },
  ],
  unscheduled: [],
  last_planned_term: 'Fall 2027',
  caveats: ['The last planned term is not a graduation date.'],
}

beforeEach(() => {
  generateSchedule.mockReset()
  validateSchedule.mockReset()
})

describe('SemesterPlanner', () => {
  it('plans on arrival and shows the backend caveats and terms', async () => {
    generateSchedule.mockResolvedValue(PLAN)
    render(<SemesterPlanner studentId="s1" />)

    expect(await screen.findByRole('region', { name: 'Spring 2027' })).toBeInTheDocument()
    expect(generateSchedule).toHaveBeenCalledWith('s1', {
      credit_target: 15,
      include_summer: false,
    })
    expect(screen.getByText('The last planned term is not a graduation date.')).toBeInTheDocument()
    expect(screen.getByText('Not a graduation date')).toBeInTheDocument()
    expect(screen.getByText('Computed by the engine')).toBeInTheDocument()
  })

  it('shows a static caveat when the backend sends none', async () => {
    generateSchedule.mockResolvedValue({ ...PLAN, caveats: [] })
    render(<SemesterPlanner studentId="s1" />)
    expect(await screen.findByText(FALLBACK_CAVEAT)).toBeInTheDocument()
  })

  it('sends a moved course to the validator and marks the error it returns', async () => {
    const user = userEvent.setup()
    generateSchedule.mockResolvedValue(PLAN)
    validateSchedule.mockResolvedValue({
      valid: false,
      issues: [
        {
          severity: 'error',
          term: 'Spring 2027',
          course: 'COSC352',
          message: 'COSC352 needs COSC220 finished in an earlier term.',
        },
      ],
    })
    render(<SemesterPlanner studentId="s1" />)

    const fall = await screen.findByRole('region', { name: 'Fall 2027' })
    await user.selectOptions(within(fall).getByRole('combobox'), 'Spring 2027')

    expect(validateSchedule).toHaveBeenCalledWith(
      's1',
      [
        { term: 'Spring 2027', courses: ['COSC220', 'COSC352'] },
        { term: 'Fall 2027', courses: [] },
      ],
      15,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('1 problem with this layout')
    const spring = screen.getByRole('region', { name: 'Spring 2027' })
    expect(
      within(spring).getByText('COSC352 needs COSC220 finished in an earlier term.'),
    ).toBeInTheDocument()
  })

  it('shows a friendly error with a retry when planning fails', async () => {
    const user = userEvent.setup()
    generateSchedule.mockRejectedValueOnce(new Error('Cannot reach the backend.'))
    generateSchedule.mockResolvedValueOnce(PLAN)
    render(<SemesterPlanner studentId="s1" />)

    expect(await screen.findByText("Couldn't build the plan")).toBeInTheDocument()
    expect(screen.getByText('Cannot reach the backend.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Fall 2027' })).toBeInTheDocument()
    expect(screen.queryByText("Couldn't build the plan")).not.toBeInTheDocument()
  })
})
