/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef3ff',
          100: '#dbe6ff',
          200: '#bdd0ff',
          300: '#93b0ff',
          400: '#6485fb',
          500: '#4560f0',
          600: '#3345e0',
          700: '#2a36c4',
          800: '#27319e',
          900: '#26307d',
        },
      },
      boxShadow: {
        soft: '0 10px 30px -12px rgba(15, 23, 42, 0.18)',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        fadeUp: 'fadeUp 0.25s ease-out both',
      },
    },
  },
  plugins: [],
}
