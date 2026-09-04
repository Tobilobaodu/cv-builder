import { cn } from "@/lib/utils"

export function Tag({
  variant = "neutral",
  className,
  children,
  ...rest
}: {
  variant?: "accent" | "accent-2" | "neutral" | "outline"
  className?: string
  children: React.ReactNode
} & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn("tag", `tag-${variant}`, className)} {...rest}>
      {children}
    </span>
  )
}
