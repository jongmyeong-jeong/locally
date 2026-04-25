import * as React from 'react'

import { cn } from '@/lib/utils'

const Input = React.forwardRef(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      ref={ref}
      className={cn(
        'flex h-9 w-full rounded-ds-input border border-ds-subtle bg-ds-card text-ds-primary',
        'px-3 py-1 text-[14px]',
        'placeholder:text-ds-disabled',
        'hover:border-ds-strong',
        'focus-visible:outline-none focus-visible:border-ds-blue focus-visible:ring-2 focus-visible:ring-ds-focus-ring focus-visible:ring-offset-0',
        'disabled:cursor-not-allowed disabled:bg-[#fafafa] disabled:text-ds-disabled',
        'file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-ds-primary',
        'transition-colors',
        className,
      )}
      {...props}
    />
  )
})
Input.displayName = 'Input'

export { Input }
