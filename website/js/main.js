/* Ashlr AO — Main JS: Mission Control */

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

  document.addEventListener('click', (e) => {
    if (!toggle.contains(e.target) && !links.contains(e.target)) {
      links.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      links.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    }
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
      }).catch(() => {});
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

// Dashboard demo animation
function initDashboardDemo() {
  // Animate cards in via JS (fallback-safe: cards are visible by default in CSS)
  const demoCards = document.querySelectorAll('.demo-card');
  if (demoCards.length) {
    demoCards.forEach(card => card.classList.add('pre-animate'));
    const cardObserver = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      cardObserver.disconnect();
      demoCards.forEach((card, i) => {
        setTimeout(() => {
          card.classList.remove('pre-animate');
          card.classList.add('animate-in');
        }, i * 150);
      });
    }, { threshold: 0.1 });
    const demoEl = document.querySelector('.dashboard-demo');
    if (demoEl) cardObserver.observe(demoEl);
  }

  const demos = [
    { el: 'demoOut0', lines: ['Writing src/auth/Login.tsx...', '+47 lines in 3 files', 'Running prettier...', 'Committing changes...'], loop: true },
    { el: 'demoOut1', lines: ['Implementing token validation...', 'Reading auth/middleware.py', 'Added JWT refresh logic', 'Writing tests...'], loop: true },
    { el: 'demoOut2', lines: ['Approve test plan? [Y/n]'], cls: 'attention' },
    { el: 'demoOut3', lines: ['\u2713 0 vulnerabilities found'], cls: 'success' },
  ];

  demos.forEach(({ el: id, lines, loop, cls }, cardIdx) => {
    const container = document.getElementById(id);
    if (!container) return;

    let i = 0;
    function addLine() {
      if (i >= lines.length) {
        if (loop) {
          setTimeout(() => { container.innerHTML = ''; i = 0; addLine(); }, 3000);
        }
        return;
      }
      const div = document.createElement('div');
      div.className = 'demo-line' + (cls ? ' ' + cls : (i > 0 ? ' dim' : ''));
      div.textContent = lines[i];
      container.appendChild(div);
      while (container.children.length > 2) container.removeChild(container.firstChild);
      i++;
      setTimeout(addLine, 1000 + Math.random() * 1000);
    }
    setTimeout(addLine, 1800 + cardIdx * 500);
  });
}

// Hero constellation network — floating tool logos with atmospheric particle field
function initHeroCanvas() {
  const canvas = document.getElementById('heroCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  let w, h, animId;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isMobile = window.innerWidth < 768;
  let mouseX = -1, mouseY = -1;
  let time = 0;

  // Tool definitions with brand colors — top nodes in upper corners, bottom nodes far left/right
  const TOOLS = [
    { name: 'Claude', color: '#D4A574', glowColor: 'rgba(212,165,116,', angle: -Math.PI * 0.65 },
    { name: 'Codex',  color: '#10A37F', glowColor: 'rgba(16,163,127,',  angle: -Math.PI * 0.35 },
    { name: 'Goose',  color: '#F472B6', glowColor: 'rgba(244,114,182,', angle: Math.PI * 0.92 },
    { name: 'Aider',  color: '#58D68D', glowColor: 'rgba(88,214,141,',  angle: Math.PI * 0.08 },
  ];

  // Load logo images
  const logos = {};
  let logosLoaded = 0;
  const logoSrcs = {
    Claude: '/public/logos/claude.svg',
    Codex:  '/public/logos/openai.svg',
    Aider:  '/public/logos/aider.svg',
    Goose:  '/public/logos/goose.svg',
  };
  for (const [name, src] of Object.entries(logoSrcs)) {
    const img = new Image();
    img.onload = () => { logosLoaded++; };
    img.src = src;
    logos[name] = img;
  }

  // Particles for ambient field
  const PARTICLE_COUNT = isMobile ? 40 : 80;
  let particles = [];

  function initParticles() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.15,
        vy: (Math.random() - 0.5) * 0.1,
        r: 0.8 + Math.random() * 1.2,
        alpha: 0.08 + Math.random() * 0.18,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  // Floating tool nodes
  let nodes = [];
  function initNodes() {
    nodes = [];
    const cx = w / 2, cy = h * 0.38;
    const orbitRx = isMobile ? w * 0.34 : Math.min(w * 0.35, 450);
    const orbitRy = isMobile ? h * 0.16 : Math.min(h * 0.17, 170);
    const count = isMobile ? 3 : TOOLS.length;
    for (let i = 0; i < count; i++) {
      const t = TOOLS[i];
      nodes.push({
        name: t.name, color: t.color, glowColor: t.glowColor,
        baseAngle: t.angle,
        angle: t.angle,
        orbitRx, orbitRy,
        cx, cy,
        speed: 0.00008 + i * 0.00002,
        size: isMobile ? 22 : 28,
        phase: i * 1.5,
        bobAmount: 3 + i * 1.5,
      });
    }
  }

  // Data streams — glowing dots that travel between nodes and center
  let streams = [];
  function emitStream(fromX, fromY, toX, toY, color, glowColor) {
    streams.push({
      fromX, fromY, toX, toY, color, glowColor,
      progress: 0,
      speed: 0.004 + Math.random() * 0.004,
      size: 2 + Math.random() * 1.5,
    });
  }

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = rect.width; h = rect.height;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    initParticles();
    initNodes();
  }

  function draw() {
    time += 16;
    ctx.clearRect(0, 0, w, h);

    const cx = w / 2, cy = h * 0.38;

    // Mouse parallax offset
    let mx = 0, my = 0;
    if (mouseX > 0 && !isMobile) {
      mx = (mouseX - w / 2) * 0.015;
      my = (mouseY - h / 2) * 0.01;
    }

    // === 1. Dot grid background ===
    const gridSpacing = isMobile ? 48 : 40;
    const gridFadeRadius = Math.min(w, h) * 0.55;
    for (let gx = gridSpacing / 2; gx < w; gx += gridSpacing) {
      for (let gy = gridSpacing / 2; gy < h; gy += gridSpacing) {
        const dx = gx - cx, dy = gy - cy;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const fade = Math.max(0, 1 - dist / gridFadeRadius);
        if (fade <= 0) continue;
        const pulse = 0.7 + Math.sin(time * 0.0008 + gx * 0.02 + gy * 0.02) * 0.3;
        ctx.globalAlpha = fade * pulse * 0.04;
        ctx.fillStyle = '#706CF0';
        ctx.beginPath();
        ctx.arc(gx + mx * 0.3, gy + my * 0.3, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // === 2. Ambient particles ===
    for (const p of particles) {
      if (!reducedMotion) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = w;
        if (p.x > w) p.x = 0;
        if (p.y < 0) p.y = h;
        if (p.y > h) p.y = 0;
      }
      const pulse = reducedMotion ? 1 : (0.6 + Math.sin(time * 0.002 + p.phase) * 0.4);
      ctx.globalAlpha = p.alpha * pulse;
      ctx.fillStyle = '#706CF0';
      ctx.beginPath();
      ctx.arc(p.x + mx * 0.5, p.y + my * 0.5, p.r, 0, Math.PI * 2);
      ctx.fill();
    }

    // === 3. Connection lines from nodes to center ===
    for (const node of nodes) {
      if (!reducedMotion) {
        node.angle += node.speed;
      }
      const bob = reducedMotion ? 0 : Math.sin(time * 0.0015 + node.phase) * node.bobAmount;
      const nx = node.cx + Math.cos(node.angle) * node.orbitRx + mx * 1.2;
      const ny = node.cy + Math.sin(node.angle) * node.orbitRy + bob + my * 0.8;
      node._x = nx;
      node._y = ny;

      // Curved connection line
      const midX = (nx + cx) / 2 + (ny - cy) * 0.15;
      const midY = (ny + cy) / 2 - (nx - cx) * 0.08;
      ctx.beginPath();
      ctx.moveTo(cx + mx, cy + my);
      ctx.quadraticCurveTo(midX + mx * 0.8, midY + my * 0.6, nx, ny);
      ctx.strokeStyle = node.color;
      ctx.globalAlpha = 0.06;
      ctx.lineWidth = 1;
      ctx.stroke();

      // Dashed overlay
      ctx.setLineDash([3, 8]);
      ctx.globalAlpha = 0.1;
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // === 4. Cross-connections between adjacent nodes ===
    for (let i = 0; i < nodes.length; i++) {
      const j = (i + 1) % nodes.length;
      const a = nodes[i], b = nodes[j];
      if (!a._x || !b._x) continue;
      ctx.beginPath();
      ctx.moveTo(a._x, a._y);
      ctx.lineTo(b._x, b._y);
      ctx.strokeStyle = '#706CF0';
      ctx.globalAlpha = 0.03;
      ctx.lineWidth = 0.5;
      ctx.stroke();
    }

    // === 5. Center hub glow ===
    const hubPulse = reducedMotion ? 1 : (0.7 + Math.sin(time * 0.001) * 0.3);
    for (let ring = 3; ring >= 1; ring--) {
      const r = 20 + ring * 20;
      const grad = ctx.createRadialGradient(cx + mx, cy + my, 0, cx + mx, cy + my, r);
      grad.addColorStop(0, 'rgba(112,108,240,0.08)');
      grad.addColorStop(1, 'rgba(112,108,240,0)');
      ctx.globalAlpha = hubPulse * (0.3 + (3 - ring) * 0.1);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx + mx, cy + my, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // === 6. Data streams ===
    if (!reducedMotion) {
      // Emit new streams randomly
      for (const node of nodes) {
        if (node._x && Math.random() < 0.002) {
          const toCenter = Math.random() > 0.4;
          emitStream(
            toCenter ? node._x : cx + mx, toCenter ? node._y : cy + my,
            toCenter ? cx + mx : node._x, toCenter ? cy + my : node._y,
            node.color, node.glowColor
          );
        }
      }
    }

    for (let i = streams.length - 1; i >= 0; i--) {
      const s = streams[i];
      s.progress += s.speed;
      if (s.progress >= 1) { streams.splice(i, 1); continue; }

      const t = s.progress;
      const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
      const sx = s.fromX + (s.toX - s.fromX) * ease;
      const sy = s.fromY + (s.toY - s.fromY) * ease;
      const alpha = t < 0.1 ? t / 0.1 : t > 0.85 ? (1 - t) / 0.15 : 1;

      // Stream glow
      const sg = ctx.createRadialGradient(sx, sy, 0, sx, sy, s.size * 6);
      sg.addColorStop(0, s.glowColor + '0.15)');
      sg.addColorStop(1, s.glowColor + '0)');
      ctx.globalAlpha = alpha;
      ctx.fillStyle = sg;
      ctx.beginPath();
      ctx.arc(sx, sy, s.size * 6, 0, Math.PI * 2);
      ctx.fill();

      // Stream core
      ctx.beginPath();
      ctx.arc(sx, sy, s.size, 0, Math.PI * 2);
      ctx.fillStyle = s.color;
      ctx.globalAlpha = alpha * 0.8;
      ctx.fill();

      // Bright center
      ctx.beginPath();
      ctx.arc(sx, sy, s.size * 0.4, 0, Math.PI * 2);
      ctx.fillStyle = '#fff';
      ctx.globalAlpha = alpha * 0.6;
      ctx.fill();
    }

    // === 7. Tool logo nodes ===
    for (const node of nodes) {
      const nx = node._x, ny = node._y;
      if (!nx) continue;
      const pulse = reducedMotion ? 1 : (0.85 + Math.sin(time * 0.002 + node.phase) * 0.15);

      // Outer glow
      const glow = ctx.createRadialGradient(nx, ny, 0, nx, ny, node.size * 3);
      glow.addColorStop(0, node.glowColor + '0.2)');
      glow.addColorStop(0.4, node.glowColor + '0.08)');
      glow.addColorStop(1, node.glowColor + '0)');
      ctx.globalAlpha = pulse;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(nx, ny, node.size * 3, 0, Math.PI * 2);
      ctx.fill();

      // Glass circle background
      ctx.globalAlpha = 0.9 * pulse;
      ctx.beginPath();
      ctx.arc(nx, ny, node.size, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(8,9,14,0.85)';
      ctx.fill();

      // Border ring
      ctx.beginPath();
      ctx.arc(nx, ny, node.size, 0, Math.PI * 2);
      ctx.strokeStyle = node.color;
      ctx.globalAlpha = 0.5 * pulse;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Logo image
      const logo = logos[node.name];
      if (logo && logo.complete && logo.naturalWidth > 0) {
        const imgSize = node.size * 1.3;
        ctx.globalAlpha = 0.9 * pulse;
        ctx.save();
        ctx.beginPath();
        ctx.arc(nx, ny, node.size - 2, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(logo, nx - imgSize / 2, ny - imgSize / 2, imgSize, imgSize);
        ctx.restore();
      } else {
        // Fallback: draw name initial
        ctx.globalAlpha = 0.9 * pulse;
        ctx.font = `bold ${isMobile ? 10 : 12}px 'JetBrains Mono', monospace`;
        ctx.fillStyle = node.color;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(node.name[0], nx, ny);
      }

      // Name label below
      ctx.globalAlpha = 0.4 * pulse;
      ctx.font = `500 ${isMobile ? 8 : 9}px 'Instrument Sans', sans-serif`;
      ctx.fillStyle = node.color;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(node.name, nx, ny + node.size + 6);
    }

    ctx.globalAlpha = 1;
    animId = requestAnimationFrame(draw);
  }

  // Intersection observer
  const observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) { if (!animId) draw(); }
    else { if (animId) { cancelAnimationFrame(animId); animId = null; } }
  }, { threshold: 0 });

  resize();

  if (reducedMotion) {
    initNodes();
    draw();
    cancelAnimationFrame(animId);
    animId = null;
  } else {
    observer.observe(canvas);
  }

  // Mouse tracking
  if (!isMobile) {
    canvas.parentElement.addEventListener('mousemove', (e) => {
      const rect = canvas.parentElement.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = e.clientY - rect.top;
    });
    canvas.parentElement.addEventListener('mouseleave', () => { mouseX = -1; mouseY = -1; });
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 200);
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
  initMobileNav();
  initSmoothScroll();
  initCopyButtons();
  initScrollAnimations();
  initCountUp();
  initDashboardDemo();
  initHeroCanvas();
  initActiveNav();
});
