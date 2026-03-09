/* Ashlr AO — Main JS: Mission Control */

// Theme toggle
function initTheme() {
  const saved = localStorage.getItem('ashlr-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
  } else if (window.matchMedia('(prefers-color-scheme: light)').matches) {
    document.documentElement.setAttribute('data-theme', 'light');
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('ashlr-theme', next);
}

// Mobile nav
function initMobileNav() {
  const toggle = document.querySelector('.nav-mobile-toggle');
  const links = document.querySelector('.nav-links');
  if (!toggle || !links) return;

  toggle.addEventListener('click', () => {
    links.classList.toggle('open');
    toggle.setAttribute('aria-expanded', links.classList.contains('open'));
  });

  links.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => links.classList.remove('open'));
  });
}

// Smooth scroll for anchor links
function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const target = document.querySelector(a.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
}

// Copy install command
function initCopyButtons() {
  document.querySelectorAll('.install-cmd').forEach(el => {
    el.addEventListener('click', () => {
      const text = el.querySelector('.cmd-text')?.textContent || el.textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        const icon = el.querySelector('.copy-icon');
        if (icon) {
          icon.textContent = 'Copied!';
          setTimeout(() => { icon.textContent = 'Copy'; }, 2000);
        }
      });
    });
  });
}

// Code block copy buttons
function initCodeCopy() {
  document.querySelectorAll('.code-block').forEach(block => {
    const btn = block.querySelector('.copy-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const code = block.querySelector('code')?.textContent || '';
      navigator.clipboard.writeText(code).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
      });
    });
  });
}

// Scroll reveal animations via IntersectionObserver
function initScrollAnimations() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

  document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
}

// Count-up animation for stats
function initCountUp() {
  const counters = document.querySelectorAll('.stat-value.counted');
  if (!counters.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const target = parseInt(el.dataset.target, 10);
      if (isNaN(target)) return;
      observer.unobserve(el);

      const duration = 1400;
      const start = performance.now();
      const format = target >= 1000;

      function tick(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        // Ease-out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(eased * target);
        el.textContent = format ? current.toLocaleString() : current;
        if (progress < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  }, { threshold: 0.5 });

  counters.forEach(el => observer.observe(el));
}

// Terminal typing animation
function initTerminalAnimation() {
  const container = document.getElementById('heroTerminal');
  if (!container) return;

  const lines = [
    { prompt: '~', cmd: 'pip install ashlr-ao', delay: 0 },
    { output: 'Successfully installed ashlr-ao-1.6.1', cls: 'success', delay: 600 },
    { prompt: '~', cmd: 'ashlr', delay: 1200 },
    { output: '    ___   ____    ____ ', cls: 'info', delay: 1700 },
    { output: '   /   | / __ \\  Ashlr AO v1.6.1', cls: 'info', delay: 1850 },
    { output: '  / /| |/ / / /  Mission Control', cls: 'info', delay: 2000 },
    { output: ' / ___ / /_/ /   http://127.0.0.1:5111', cls: 'dim', delay: 2150 },
    { output: '', cls: 'dim', delay: 2400 },
    { output: '✓ 4 backends ready', cls: 'success', delay: 2600 },
    { output: '✓ Dashboard live — Cmd+K to begin', cls: 'success', delay: 2900 },
    { output: '', cls: 'dim', delay: 3200 },
    { prompt: '~', cmd: 'ashlr spawn --role backend --task "Build auth API"', delay: 3400 },
    { output: '⚡ Agent "auth-api" spawned (claude-code, tmux)', cls: 'info', delay: 4200 },
    { output: '   Status: working — reading project structure...', cls: 'dim', delay: 4600 },
  ];

  lines.forEach((line, i) => {
    setTimeout(() => {
      const div = document.createElement('div');
      div.className = 'terminal-line';
      div.style.animationDelay = '0s';

      if (line.prompt) {
        div.innerHTML = `<span class="prompt">${line.prompt} $</span> <span class="cmd">${line.cmd}</span>`;
      } else {
        div.innerHTML = `<span class="${line.cls || 'output'}">${line.output || '&nbsp;'}</span>`;
      }

      container.appendChild(div);
      // Auto-scroll terminal
      container.scrollTop = container.scrollHeight;
    }, line.delay);
  });
}

// Active nav link
function initActiveNav() {
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  document.querySelectorAll('.nav-links a, .docs-nav-group a').forEach(a => {
    const href = a.getAttribute('href')?.replace(/\/$/, '') || '/';
    if (href === path || (path.startsWith(href) && href !== '/')) {
      a.classList.add('active');
    }
  });
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initMobileNav();
  initSmoothScroll();
  initCopyButtons();
  initCodeCopy();
  initScrollAnimations();
  initCountUp();
  initTerminalAnimation();
  initActiveNav();
});

// Apply theme before paint
initTheme();
