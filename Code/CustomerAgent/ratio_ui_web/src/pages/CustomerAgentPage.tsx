import { useEffect, useRef } from 'react';

/**
 * CustomerAgentPage — renders the Customer Agent SPA inline.
 *
 * Loads the SPA's HTML from /customer-agent/index.html, then injects the
 * styles and script. The SPA's API calls go through the Vite proxy
 * at /customer-agent-api → http://127.0.0.1:8020.
 *
 * This gives exact visual parity with the standalone CustomerAgent UI.
 */
export default function CustomerAgentPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (loadedRef.current || !containerRef.current) return;
    loadedRef.current = true;

    const container = containerRef.current;

    (async () => {
      try {
        // Fetch the SPA HTML
        const res = await fetch('/customer-agent/index.html');
        const html = await res.text();

        // Extract the <body> content (between <body> and </body>)
        const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
        if (!bodyMatch) return;

        let bodyContent = bodyMatch[1];
        // Remove the <script> tag — we'll load it separately after DOM is ready
        bodyContent = bodyContent.replace(/<script[^>]*src="app\.js"[^>]*><\/script>/gi, '');

        // Inject the HTML
        container.innerHTML = bodyContent;

        // Load the CSS
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/customer-agent/styles.css';
        document.head.appendChild(link);

        // Load Font Awesome (if not already present)
        if (!document.querySelector('link[href*="font-awesome"]')) {
          const fa = document.createElement('link');
          fa.rel = 'stylesheet';
          fa.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css';
          document.head.appendChild(fa);
        }

        // Load the SPA JS after a tick so the DOM elements exist
        const script = document.createElement('script');
        script.src = '/customer-agent/app.js';
        script.async = true;
        document.body.appendChild(script);
      } catch (err) {
        container.innerHTML = `
          <div style="padding: 48px; text-align: center; color: #666;">
            <h3>Customer Agent UI could not load</h3>
            <p>Make sure the backend is running on port 8020.</p>
            <code>cd src/services/ratio_customer_health && PORT=8020 python -m uvicorn reasoningagent.app:app --host 127.0.0.1 --port 8020</code>
          </div>
        `;
      }
    })();

    // Cleanup on unmount
    return () => {
      document.querySelectorAll('link[href="/customer-agent/styles.css"]').forEach(el => el.remove());
      document.querySelectorAll('script[src="/customer-agent/app.js"]').forEach(el => el.remove());
    };
  }, []);

  return (
    <div
      ref={containerRef}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        overflow: 'hidden',
      }}
    />
  );
}
