import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-ds-control transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ds-focus-ring focus-visible:ring-offset-2 disabled:pointer-events-none',
  {
    variants: {
      variant: {
        // primary — bg-ds-action (#171717) / text-ds-on-action (#ffffff)
        default:
          'bg-ds-action text-ds-on-action hover:bg-[#2a2a2a] active:bg-[#0a0a0a] disabled:bg-[#e5e5e5] disabled:text-[#a3a3a3] disabled:cursor-not-allowed',
        // destructive — white card bg, red text/border
        destructive:
          'bg-ds-card text-ds-error border border-ds-error hover:bg-[#fef2f2] hover:text-[#b91c1c] hover:border-[#b91c1c] active:bg-[#fee2e2] active:text-[#b91c1c] active:border-[#b91c1c] disabled:text-[#fecaca] disabled:border-[#fecaca]',
        // outline → secondary — white bg, subtle border
        outline:
          'bg-ds-card text-ds-primary border border-ds-subtle hover:bg-[#fafafa] hover:border-ds-strong active:bg-ds-selected active:border-ds-strong disabled:bg-[#fafafa] disabled:text-[#c4c4c4] disabled:border-[rgba(0,0,0,0.05)]',
        // secondary → same visuals as outline/secondary
        secondary:
          'bg-ds-card text-ds-primary border border-ds-subtle hover:bg-[#fafafa] hover:border-ds-strong active:bg-ds-selected active:border-ds-strong disabled:bg-[#fafafa] disabled:text-[#c4c4c4] disabled:border-[rgba(0,0,0,0.05)]',
        // ghost — transparent bg, primary text
        ghost:
          'bg-transparent text-ds-primary hover:bg-ds-hover active:bg-[rgba(0,0,0,0.08)] disabled:bg-transparent disabled:text-[#c4c4c4]',
        // link — no bg, underline on hover
        link: 'text-ds-link underline-offset-4 hover:underline',
      },
      size: {
        // md (default) — h-9 (36px), px-[14px], 14px/500
        default: 'h-9 px-[14px] text-[14px] font-medium',
        // sm — h-8 (32px), px-[10px], 14px/500
        sm: 'h-8 px-[10px] text-[14px] font-medium',
        // lg — h-12 (48px), px-5 (20px), 15px/600
        lg: 'h-12 px-5 text-[15px] font-semibold',
        // icon — 32×32 square, no padding
        icon: 'h-8 w-8 p-0',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

const Button = React.forwardRef(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

export { Button, buttonVariants }
