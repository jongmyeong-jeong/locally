import * as React from 'react'

import { cn } from '@/lib/utils'

const Textarea = React.forwardRef(({ className, ...props }, ref) => {
  return (
    <textarea
      ref={ref}
      className={cn(
        'flex min-h-[60px] w-full rounded-ds-input border border-ds-subtle bg-ds-card text-ds-primary px-3 py-2 text-[14px] placeholder:text-ds-disabled hover:border-ds-strong focus-visible:outline-none focus-visible:border-ds-blue focus-visible:ring-2 focus-visible:ring-ds-focus-ring focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:bg-[#fafafa] disabled:text-ds-disabled transition-colors resize-y',
        className,
      )}
      {...props}
    />
  )
})
Textarea.displayName = 'Textarea'

export { Textarea }
