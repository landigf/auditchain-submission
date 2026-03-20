/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#f0f4ff',
          100: '#e0e9ff',
          500: '#3b5bdb',
          600: '#2f4ac4',
          700: '#1a3299',
          900: '#0f1f5c',
        },
        audit: {
          ready:  '#22c55e',
          review: '#f59e0b',
          manual: '#ef4444',
        },
      },
    },
  },
  plugins: [],
}
