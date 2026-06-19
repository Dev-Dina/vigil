import { Analytics } from '@vercel/analytics/next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import type { Metadata } from 'next'
import { AppChrome } from '@/components/vigil'
import { AuthProvider } from '@/lib/auth-context'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Vigil — Clinical Trial Retention Intelligence',
  description:
    'Retention intelligence for clinical trials — surfaces and explains dropout risk early so teams can intervene before a participant disengages.',
  icons: {
    icon: [
      { url: '/vigil-mark-32.png', sizes: '32x32', type: 'image/png' },
      { url: '/vigil-mark.png', sizes: '256x256', type: 'image/png' },
    ],
    apple: '/vigil-mark.png',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} bg-background`}>
      <body className="font-sans antialiased">
        <AuthProvider>
          <AppChrome>{children}</AppChrome>
        </AuthProvider>
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
