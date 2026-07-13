import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import SafeHtml from '../SafeHtml';

describe('SafeHtml', () => {
  it('should render safe HTML content', () => {
    render(<SafeHtml html="<p>Hello World</p>" />);
    expect(screen.getByText('Hello World')).toBeInTheDocument();
  });

  it('should sanitize XSS content (script tag removed)', () => {
    render(<SafeHtml html="<p>Safe</p><script>alert('xss')</script>" />);
    expect(screen.getByText('Safe')).toBeInTheDocument();
    // script tag should be stripped by dompurify
    expect(document.querySelector('script')).toBeNull();
  });

  it('should sanitize onclick handlers', () => {
    render(<SafeHtml html="<div onclick='alert(1)'>Click me</div>" />);
    const div = screen.getByText('Click me');
    expect(div).toBeInTheDocument();
    // onclick should be stripped by dompurify
    expect(div.getAttribute('onclick')).toBeNull();
  });

  it('should apply custom className', () => {
    const { container } = render(
      <SafeHtml html="<p>Test</p>" className="custom-class" />
    );
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper).toHaveClass('custom-class');
  });

  it('should render as custom tag', () => {
    const { container } = render(
      <SafeHtml html="<span>Inline</span>" tag="section" />
    );
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper.tagName).toBe('SECTION');
  });

  it('should render as div by default', () => {
    const { container } = render(<SafeHtml html="<p>Default</p>" />);
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper.tagName).toBe('DIV');
  });

  it('should handle empty HTML', () => {
    const { container } = render(<SafeHtml html="" />);
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper).toBeInTheDocument();
    expect(wrapper.innerHTML).toBe('');
  });

  it('should handle complex nested HTML', () => {
    render(
      <SafeHtml html="<ul><li>Item 1</li><li>Item 2</li></ul>" />
    );
    expect(screen.getByText('Item 1')).toBeInTheDocument();
    expect(screen.getByText('Item 2')).toBeInTheDocument();
  });
});
