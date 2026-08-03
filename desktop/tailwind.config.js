/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        abyss: {
          950: '#07090e',
          900: '#0a0e14',
          850: '#0d1219',
          800: '#111722',
          750: '#151d2b',
          700: '#1b2434',
          600: '#243048',
        },
        neon: {
          300: '#7df9dd',
          400: '#4ef0c8',
          500: '#2ee6ae',
          600: '#17c793',
        },
        amberx: {
          400: '#ffc46b',
          500: '#f5a83d',
        },
        bloodx: {
          400: '#ff7a72',
          500: '#f0524a',
        },
        think: {
          300: '#b9aee0',
          400: '#9a8cc7',
          500: '#7c6ea8',
        },
      },
      fontFamily: {
        display: ['"Chakra Petch"', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        sans: ['"PingFang SC"', '"Hiragino Sans GB"', '"Microsoft YaHei"', '"Noto Sans CJK SC"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'glow-neon': '0 0 12px rgba(46, 230, 174, 0.25), 0 0 2px rgba(46, 230, 174, 0.5)',
        'glow-amber': '0 0 10px rgba(245, 168, 61, 0.18)',
        'glow-red': '0 0 10px rgba(240, 82, 74, 0.22)',
      },
    },
  },
  plugins: [],
}
