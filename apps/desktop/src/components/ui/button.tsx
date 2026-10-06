import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx } from "clsx";

// 沿用 shadcn/ui 的可组合按钮模式，视觉变量由应用统一维护。
const variants = cva("button", {
  variants: {
    variant: {
      default: "primary",
      secondary: "secondary",
      ghost: "ghost",
      danger: "danger",
    },
  },
  defaultVariants: { variant: "default" },
});
export function Button({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof variants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : "button";
  return (
    <Component className={clsx(variants({ variant }), className)} {...props} />
  );
}
