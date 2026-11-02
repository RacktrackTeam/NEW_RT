import { useEffect, useState } from 'react';

/** 1024 px and up is the laptop layout; below it is the phone layout. */
export const LAPTOP_QUERY = '(min-width: 1024px)';

function matches() {
  try { return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(LAPTOP_QUERY).matches; } catch { return false; }
}

export function useIsLaptop() {
  const [laptop, setLaptop] = useState(matches);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const mq = window.matchMedia(LAPTOP_QUERY);
    const on = () => setLaptop(mq.matches);
    on();
    if (mq.addEventListener) mq.addEventListener('change', on); else mq.addListener(on);
    return () => { if (mq.removeEventListener) mq.removeEventListener('change', on); else mq.removeListener(on); };
  }, []);
  return laptop;
}

export default useIsLaptop;
