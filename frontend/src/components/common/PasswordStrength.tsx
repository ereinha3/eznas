/** Visual password strength indicator bar. */

interface PasswordStrengthProps {
  password: string
}

interface StrengthResult {
  score: number // 0-4
  label: string
  color: string
}

function evaluateStrength(password: string): StrengthResult {
  if (!password) return { score: 0, label: '', color: '' }

  let score = 0

  // Length checks
  if (password.length >= 8) score++
  if (password.length >= 12) score++
  if (password.length >= 16) score++

  // Character variety
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++
  if (/\d/.test(password)) score++
  if (/[^a-zA-Z0-9]/.test(password)) score++

  // Common patterns (penalty)
  if (/^(123|abc|password|qwerty|admin)/i.test(password)) score = Math.max(score - 2, 0)
  if (/(.)\1{2,}/.test(password)) score = Math.max(score - 1, 0) // repeated chars

  // Normalize to 0-4
  const normalized = Math.min(Math.max(Math.round(score * (4 / 6)), 0), 4)

  const levels: StrengthResult[] = [
    { score: 0, label: 'Too weak', color: 'var(--color-error)' },
    { score: 1, label: 'Weak', color: 'var(--color-error)' },
    { score: 2, label: 'Fair', color: 'var(--color-warning)' },
    { score: 3, label: 'Good', color: 'var(--color-success)' },
    { score: 4, label: 'Strong', color: 'var(--color-success-light)' },
  ]

  return levels[normalized]
}

export function PasswordStrength({ password }: PasswordStrengthProps) {
  const result = evaluateStrength(password)

  if (!password) return null

  return (
    <div className="password-strength">
      <div className="password-strength-bar">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="password-strength-segment"
            style={{
              backgroundColor: i < result.score ? result.color : 'var(--surface-slate-20)',
            }}
          />
        ))}
      </div>
      <span
        className="password-strength-label"
        style={{ color: result.color }}
      >
        {result.label}
      </span>
    </div>
  )
}
