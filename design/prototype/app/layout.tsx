import type { Metadata } from 'next';
import './globals.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: 'Проверка контрагентов — Альфа-Банк',
  description:
    'Моковый прототип сервиса проверки контрагентов с объяснимыми выводами.',
  openGraph: {
    title: 'Проверка контрагентов',
    description: 'Факты. Риски. Источники.',
    images: ['/og.png'],
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Проверка контрагентов',
    description: 'Факты. Риски. Источники.',
    images: ['/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
