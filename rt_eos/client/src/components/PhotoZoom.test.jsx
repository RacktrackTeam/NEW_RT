import { describe, test, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PhotoZoom } from './PhotoZoom.jsx';

const sized = (img) => { Object.defineProperty(img, 'clientWidth', { value: 120 }); return img; };

describe('PhotoZoom', () => {
  test('a tap on a picture opens a closer look, Close shuts it', () => {
    render(<><PhotoZoom /><img src="/rack.jpg" alt="Rack B12" /></>);
    fireEvent.click(sized(screen.getByAltText('Rack B12')));
    expect(screen.getByRole('dialog', { name: 'Closer look' })).toBeTruthy();
    expect(screen.getByAltText('Closer look').getAttribute('src')).toContain('/rack.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: 'Closer look' })).toBeNull();
  });

  test('a picture inside a button or marked data-nozoom keeps its own action', () => {
    render(<><PhotoZoom /><button type="button"><img src="/a.jpg" alt="In a button" /></button><img src="/logo.jpg" alt="Logo" data-nozoom="" /></>);
    fireEvent.click(sized(screen.getByAltText('In a button')));
    fireEvent.click(sized(screen.getByAltText('Logo')));
    expect(screen.queryByRole('dialog', { name: 'Closer look' })).toBeNull();
  });
});
