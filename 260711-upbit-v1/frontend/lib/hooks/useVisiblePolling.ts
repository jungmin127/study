'use client';

import { useEffect } from 'react';

/**
 * callback을 즉시 1회 실행한 뒤 intervalMs마다 반복 호출한다. 단, 탭/화면이 백그라운드로
 * 전환된 동안(document.visibilityState !== 'visible')에는 네트워크 호출을 건너뛰어
 * 모바일 LTE 데이터·배터리 소모를 줄인다. 다시 화면으로 돌아오면 그 즉시 한 번 더
 * 호출해 오래된 데이터가 보이지 않게 한다. enabled가 false면 아무 것도 하지 않는다
 * (예: GridSearchPage가 실행 중인 job이 없을 때 폴링 자체를 끄는 기존 동작 유지).
 */
export function useVisiblePolling(callback: () => void, intervalMs: number, enabled = true) {
  useEffect(() => {
    if (!enabled) return;

    callback();

    const id = setInterval(() => {
      if (document.visibilityState === 'visible') callback();
    }, intervalMs);

    function handleVisibilityChange() {
      if (document.visibilityState === 'visible') callback();
    }
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [callback, intervalMs, enabled]);
}
