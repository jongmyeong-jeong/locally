import * as React from 'react'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const pillVariants = cva(
  'inline-flex items-center rounded-ds-pill px-2 py-[2px] text-ds-caption whitespace-nowrap',
  {
    variants: {
      variant: {
        neutral: 'bg-ds-pill-neutral text-ds-pill-neutral-fg',
        info:    'bg-ds-pill-info text-ds-pill-info-fg',
        success: 'bg-ds-pill-success text-ds-pill-success-fg',
        warning: 'bg-ds-pill-warning text-ds-pill-warning-fg',
        error:   'bg-ds-pill-error text-ds-pill-error-fg',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

const Pill = React.forwardRef(({ className, variant, ...props }, ref) => (
  <span ref={ref} className={cn(pillVariants({ variant }), className)} {...props} />
))
Pill.displayName = 'Pill'

export { Pill, pillVariants }
