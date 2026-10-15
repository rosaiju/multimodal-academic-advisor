import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SignIn from './SignIn'
import { login, register } from '../api'

vi.mock('../api', () => ({ login: vi.fn(), register: vi.fn() }))

beforeEach(() => {
  login.mockReset()
  register.mockReset()
})

async function fillIn(user, { email, password }) {
  await user.type(screen.getByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
}

describe('SignIn', () => {
  it('shows the error message when sign-in fails', async () => {
    const user = userEvent.setup()
    login.mockRejectedValue(new Error('Incorrect email or password'))
    const onSignedIn = vi.fn()
    render(<SignIn onSignedIn={onSignedIn} />)

    await fillIn(user, { email: 'student@morgan.edu', password: 'wrong-password' })
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Incorrect email or password')).toBeInTheDocument()
    expect(screen.getByText('Could not sign in')).toBeInTheDocument()
    expect(onSignedIn).not.toHaveBeenCalled()
    // The button comes back so the student can try again.
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('signs in with the typed credentials and passes the user on', async () => {
    const user = userEvent.setup()
    const account = { id: 'stu_1', email: 'student@morgan.edu' }
    login.mockResolvedValue(account)
    const onSignedIn = vi.fn()
    render(<SignIn onSignedIn={onSignedIn} />)

    await fillIn(user, { email: 'student@morgan.edu', password: 'correct horse battery' })
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(login).toHaveBeenCalledWith({
      email: 'student@morgan.edu',
      password: 'correct horse battery',
    })
    expect(register).not.toHaveBeenCalled()
    expect(onSignedIn).toHaveBeenCalledWith(account)
  })
})
