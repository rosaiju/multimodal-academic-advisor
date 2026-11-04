import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CourseCompare from './CourseCompare'
import { compareCourses, getCatalogCourses } from '../api'

vi.mock('../api', () => ({ compareCourses: vi.fn(), getCatalogCourses: vi.fn() }))

const CATALOG = [
  { code: 'COSC220', title: 'Data Structures and Algorithms' },
  { code: 'COSC281', title: 'Discrete Structure' },
  { code: 'COSC352', title: 'Organization of Programming Languages' },
]

const entry = (code, state, extra = {}) => ({
  code,
  title: `${code} title`,
  credits: '3',
  state,
  prerequisites: [],
  missing_prerequisites: [],
  serves: [],
  unlocks: [],
  remaining_chain: 0,
  terms_offered: ['fall', 'spring'],
  terms_assumed: true,
  warnings: [],
  ...extra,
})

beforeEach(() => {
  compareCourses.mockReset()
  getCatalogCourses.mockReset()
})

describe('CourseCompare', () => {
  it('compares the picked courses and shows each one for this student', async () => {
    const user = userEvent.setup()
    getCatalogCourses.mockResolvedValue(CATALOG)
    compareCourses.mockResolvedValue({
      courses: [
        entry('COSC220', 'eligible_now', { unlocks: ['COSC352'] }),
        entry('COSC352', 'blocked', {
          prerequisites: ['COSC220'],
          missing_prerequisites: ['COSC220'],
        }),
      ],
      differences: ['Only COSC220 can be taken now.'],
      caveats: ['Offering terms are assumed.'],
    })
    render(<CourseCompare studentId="s1" programId="morgan" />)

    const picker = await screen.findByLabelText('Add a course')
    expect(screen.getByText('Choose at least two courses to compare.')).toBeInTheDocument()
    await user.selectOptions(picker, 'COSC220')
    expect(compareCourses).not.toHaveBeenCalled()
    await user.selectOptions(screen.getByLabelText('Add a course'), 'COSC352')

    expect(compareCourses).toHaveBeenCalledWith('s1', ['COSC220', 'COSC352'])
    expect(await screen.findByText('Only COSC220 can be taken now.')).toBeInTheDocument()
    expect(screen.getByText('You can take it now')).toBeInTheDocument()
    expect(screen.getByText('Prerequisites needed')).toBeInTheDocument()
    expect(screen.getByText('Still needed: COSC220')).toBeInTheDocument()
    expect(screen.getByText('Offering terms are assumed.')).toBeInTheDocument()
    expect(screen.getByText('Computed by the engine')).toBeInTheDocument()
  })

  it('says so when the course list cannot be loaded', async () => {
    getCatalogCourses.mockRejectedValue(new Error('Cannot reach the backend.'))
    render(<CourseCompare studentId="s1" programId="morgan" />)
    expect(await screen.findByText("Couldn't load the course list")).toBeInTheDocument()
    expect(screen.queryByLabelText('Add a course')).not.toBeInTheDocument()
  })

  it('shows the compare error and drops a removed course', async () => {
    const user = userEvent.setup()
    getCatalogCourses.mockResolvedValue(CATALOG)
    compareCourses.mockRejectedValue(new Error('Unknown course'))
    render(<CourseCompare studentId="s1" programId="morgan" />)

    await user.selectOptions(await screen.findByLabelText('Add a course'), 'COSC220')
    await user.selectOptions(screen.getByLabelText('Add a course'), 'COSC281')
    expect(await screen.findByText("Couldn't compare those")).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove COSC281' }))
    expect(screen.queryByRole('button', { name: 'Remove COSC281' })).not.toBeInTheDocument()
  })
})
