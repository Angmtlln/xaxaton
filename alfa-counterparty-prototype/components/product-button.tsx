'use client';

import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

type ProductButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  view?: 'accent' | 'primary' | 'secondary';
  controlSize?: 48 | 56;
  leftAddons?: ReactNode;
};

export function ProductButton({
  children,
  className,
  view = 'primary',
  controlSize = 48,
  leftAddons,
  ...props
}: ProductButtonProps) {
  return (
    <button
      className={cn(
        'rounded-none px-5 text-sm font-bold shadow-none',
        controlSize === 56 ? 'h-14' : 'h-12',
        view === 'accent' && 'bg-[#ef3124] text-white hover:bg-[#d92519]',
        view === 'primary' && 'bg-[#111111] text-white hover:bg-[#292929]',
        view === 'secondary' && 'bg-[#e7e7e7] text-[#111111] hover:bg-[#d9d9d9]',
        className,
      )}
      {...props}
    >
      {leftAddons}
      {children}
    </button>
  );
}
