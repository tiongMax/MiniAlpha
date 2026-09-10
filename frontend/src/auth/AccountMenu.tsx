import { useEffect, useState, type FormEvent } from 'react'
import { LogOut, UserRound, X } from 'lucide-react'
import type { AccountController } from './useAccount'

export function AccountMenu({ controller }: { controller: AccountController }) {
  const [open, setOpen] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', close)
    document.querySelector<HTMLInputElement>('.account-dialog input')?.focus()
    return () => window.removeEventListener('keydown', close)
  }, [open, registering])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const email = String(data.get('email') ?? '')
    const password = String(data.get('password') ?? '')
    const action = registering
      ? controller.register(String(data.get('displayName') ?? ''), email, password)
      : controller.login(email, password)
    setPending(true)
    setError(null)
    void action
      .then(() => setOpen(false))
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : 'Account request failed.'),
      )
      .finally(() => setPending(false))
  }

  if (controller.account) {
    return (
      <div className="account-menu signed-in">
        <button className="account-button" onClick={() => setOpen((value) => !value)}>
          <UserRound size={15} />
          <span>{controller.account.display_name}</span>
        </button>
        {open && (
          <div className="account-popover">
            <strong>{controller.account.display_name}</strong>
            <small>{controller.account.email}</small>
            {controller.account.auth_mode === 'single_user' ? (
              <span className="subtle-badge">Single-user mode</span>
            ) : (
              <button
                className="text-button"
                disabled={pending}
                onClick={() => {
                  setPending(true)
                  void controller.logout().finally(() => {
                    setPending(false)
                    setOpen(false)
                  })
                }}
              >
                <LogOut size={13} /> Sign out
              </button>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="account-menu">
      <button
        className="account-button"
        disabled={controller.loading || controller.unavailable}
        title={controller.unavailable ? 'Account services are unavailable' : undefined}
        onClick={() => setOpen(true)}
      >
        <UserRound size={15} />
        <span>{controller.loading ? 'Checking…' : 'Sign in'}</span>
      </button>
      {open && (
        <div className="account-dialog-backdrop" onMouseDown={() => setOpen(false)}>
          <section
            className="account-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="account-dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <button
              className="icon-button account-close"
              aria-label="Close account dialog"
              onClick={() => setOpen(false)}
            >
              <X size={17} />
            </button>
            <span className="eyebrow">MINIALPHA ACCOUNT</span>
            <h2 id="account-dialog-title">
              {registering ? 'Create your account' : 'Welcome back'}
            </h2>
            <p>
              {registering
                ? 'Create an identity for your future cross-device research workspace.'
                : 'Sign in to your MiniAlpha research workspace.'}
            </p>
            <form onSubmit={submit}>
              {registering && (
                <label>
                  Display name
                  <input name="displayName" autoComplete="name" maxLength={80} required />
                </label>
              )}
              <label>
                Email
                <input name="email" type="email" autoComplete="email" maxLength={254} required />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete={registering ? 'new-password' : 'current-password'}
                  minLength={12}
                  maxLength={128}
                  required
                />
              </label>
              {error && (
                <p className="inline-error" role="alert">
                  {error}
                </p>
              )}
              <button className="primary-button" disabled={pending}>
                {pending ? 'Please wait…' : registering ? 'Create account' : 'Sign in'}
              </button>
            </form>
            <button
              className="text-button account-mode"
              onClick={() => {
                setRegistering((value) => !value)
                setError(null)
              }}
            >
              {registering
                ? 'Already have an account? Sign in'
                : 'New to MiniAlpha? Create account'}
            </button>
          </section>
        </div>
      )}
    </div>
  )
}
