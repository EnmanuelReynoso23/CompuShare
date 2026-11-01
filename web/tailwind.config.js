/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#0b0a08',
          soft: '#141310',
          panel: '#17160f',
        },
        paper: {
          DEFAULT: '#f2efe6',
          dim: '#a9a599',
          faint: '#6b675d',
        },
        line: {
          DEFAULT: '#2c2a23',
          strong: '#4a473c',
        },
      },
      fontFamily: {
        display: ['"Space Mono"', 'ui-monospace', 'monospace'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
