import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Quant Trading',
    short_name: 'Quant Trading',
    description: '백테스트·그리드서치·라이브 전략·매매일지 대시보드',
    start_url: '/journal',
    display: 'standalone',
    background_color: '#0a0a0a',
    theme_color: '#2f6fee',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
