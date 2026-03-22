/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        r: {
          bg:      '#070711',
          surface: '#0d0d1a',
          raised:  '#121228',
          panel:   '#0f0f1e',
          border:  '#1c1c34',
          bright:  '#2a2a4a',
          text:    '#c0c0e0',
          dim:     '#4a4a70',
          muted:   '#22223a',
          cyan:    '#00e5ff',
          'cyan-d':'#003a50',
          red:     '#ff2d55',
          'red-d': '#3a0014',
          amber:   '#ffaa00',
          'amb-d': '#3a2800',
          green:   '#00e676',
          'grn-d': '#003a1e',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Cascadia Code', 'Fira Code', 'monospace'],
        ui:   ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'dot-pulse':    'dotPulse 1.4s ease-in-out infinite',
        'slide-up':     'slideUp 0.25s ease-out',
        'incident-in':  'incidentIn 0.6s ease-out',
        'bg-flash':     'bgFlash 0.7s ease-in-out 3',
        'fade-in':      'fadeIn 0.3s ease-out',
      },
      keyframes: {
        dotPulse: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.2' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        incidentIn: {
          '0%':   { opacity: '0', transform: 'scale(0.97)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        bgFlash: {
          '0%, 100%': { backgroundColor: 'transparent' },
          '50%':      { backgroundColor: 'rgba(255,45,85,0.04)' },
        },
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
