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

// Hero agent orchestration network
function initHeroCanvas() {
  const canvas = document.getElementById('heroCanvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  let w, h, animId;

  // Agent nodes — roles with colors matching the product
  const ROLES = [
    { label: 'FE', color: '#8B5CF6', angle: 0 },
    { label: 'BE', color: '#3B82F6', angle: Math.PI / 3 },
    { label: 'QA', color: '#22C55E', angle: (2 * Math.PI) / 3 },
    { label: 'SEC', color: '#EF4444', angle: Math.PI },
    { label: 'OPS', color: '#F97316', angle: (4 * Math.PI) / 3 },
    { label: 'DOC', color: '#A855F7', angle: (5 * Math.PI) / 3 },
    { label: 'ARC', color: '#06B6D4', angle: Math.PI / 6 },
    { label: 'REV', color: '#EAB308', angle: (7 * Math.PI) / 6 },
  ];

  let nodes = [];
  let dataPackets = [];
  let spawnTimer = 0;
  let mouseX = -1, mouseY = -1;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isMobile = window.innerWidth < 768;
  const NODE_COUNT = isMobile ? 5 : ROLES.length;

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = rect.width;
    h = rect.height;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  const HUB = { x: 0, y: 0 }; // updated each frame

  function initNodes() {
    nodes = [];
    const baseRadius = Math.min(w, h) * (isMobile ? 0.28 : 0.25);
    for (let i = 0; i < NODE_COUNT; i++) {
      const role = ROLES[i];
      const orbitRadius = baseRadius + (i % 2) * (baseRadius * 0.35);
      nodes.push({
        label: role.label,
        color: role.color,
        angle: role.angle + (Math.random() - 0.5) * 0.3,
        orbitRadius,
        orbitSpeed: 0.0003 + Math.random() * 0.0002,
        r: isMobile ? 16 : 20,
        phase: Math.random() * Math.PI * 2,
        status: 'working', // working | complete | spawning
        statusTimer: Math.random() * 400,
        spawnProgress: 1, // 0→1 during spawn animation
      });
    }
  }

  function spawnNode() {
    if (nodes.length >= NODE_COUNT) return;
    const role = ROLES[nodes.length % ROLES.length];
    const baseRadius = Math.min(w, h) * (isMobile ? 0.28 : 0.25);
    nodes.push({
      label: role.label,
      color: role.color,
      angle: role.angle + (Math.random() - 0.5) * 0.3,
      orbitRadius: baseRadius + (nodes.length % 2) * (baseRadius * 0.35),
      orbitSpeed: 0.0003 + Math.random() * 0.0002,
      r: isMobile ? 16 : 20,
      phase: Math.random() * Math.PI * 2,
      status: 'spawning',
      statusTimer: 0,
      spawnProgress: 0,
    });
  }

  function addPacket(fromX, fromY, toX, toY, color) {
    dataPackets.push({
      fromX, fromY, toX, toY, color,
      progress: 0, speed: 0.015 + Math.random() * 0.01,
    });
  }

  let time = 0;
  function draw() {
    time += 16;
    ctx.clearRect(0, 0, w, h);

    HUB.x = w / 2;
    HUB.y = h * (isMobile ? 0.42 : 0.45);

    // === Central hub ===
    // Concentric rings
    for (let i = 3; i >= 1; i--) {
      const ringR = 30 + i * 12;
      const ringAlpha = 0.04 + (3 - i) * 0.02;
      const pulseScale = 1 + Math.sin(time * 0.001 + i) * 0.03;
      ctx.beginPath();
      ctx.arc(HUB.x, HUB.y, ringR * pulseScale, 0, Math.PI * 2);
      ctx.strokeStyle = '#706CF0';
      ctx.globalAlpha = ringAlpha;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // Hub glow
    const hubGrad = ctx.createRadialGradient(HUB.x, HUB.y, 0, HUB.x, HUB.y, 60);
    hubGrad.addColorStop(0, 'rgba(112, 108, 240, 0.25)');
    hubGrad.addColorStop(1, 'rgba(112, 108, 240, 0)');
    ctx.globalAlpha = 0.6 + Math.sin(time * 0.002) * 0.15;
    ctx.fillStyle = hubGrad;
    ctx.beginPath();
    ctx.arc(HUB.x, HUB.y, 60, 0, Math.PI * 2);
    ctx.fill();

    // Hub core
    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.arc(HUB.x, HUB.y, 18, 0, Math.PI * 2);
    ctx.fillStyle = '#706CF0';
    ctx.globalAlpha = 0.9;
    ctx.fill();

    // Hub label
    ctx.globalAlpha = 1;
    ctx.font = `bold ${isMobile ? 10 : 11}px 'JetBrains Mono', monospace`;
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('AO', HUB.x, HUB.y);

    // === Update & draw nodes ===
    for (const node of nodes) {
      // Update spawn progress
      if (node.status === 'spawning') {
        node.spawnProgress = Math.min(1, node.spawnProgress + 0.02);
        if (node.spawnProgress >= 1) node.status = 'working';
      }

      // Status transitions
      node.statusTimer += 16;
      if (!reducedMotion && node.statusTimer > 5000 + Math.random() * 3000 && node.status === 'working') {
        node.status = 'complete';
        node.statusTimer = 0;
      } else if (node.status === 'complete' && node.statusTimer > 2000) {
        node.status = 'working';
        node.statusTimer = 0;
      }

      // Orbit
      if (!reducedMotion) {
        node.angle += node.orbitSpeed * (node.status === 'working' ? 1 : 0.3);
      }

      const effectiveRadius = node.orbitRadius * node.spawnProgress;
      let nx = HUB.x + Math.cos(node.angle) * effectiveRadius;
      let ny = HUB.y + Math.sin(node.angle) * effectiveRadius;

      // Mouse parallax
      if (mouseX > 0 && !isMobile) {
        const mdx = mouseX - nx, mdy = mouseY - ny;
        const mDist = Math.sqrt(mdx * mdx + mdy * mdy);
        if (mDist < 200) {
          const push = (1 - mDist / 200) * 15;
          nx -= (mdx / mDist) * push;
          ny -= (mdy / mDist) * push;
        }
      }

      // === Connection line (hub ↔ node) ===
      ctx.beginPath();
      ctx.setLineDash([4, 6]);
      ctx.moveTo(HUB.x, HUB.y);
      ctx.lineTo(nx, ny);
      ctx.strokeStyle = node.color;
      ctx.globalAlpha = 0.12 * node.spawnProgress;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);

      // === Node ===
      const pulse = reducedMotion ? 1 : (Math.sin(time * 0.003 + node.phase) * 0.15 + 0.85);

      // Node glow
      const nodeGlow = ctx.createRadialGradient(nx, ny, 0, nx, ny, node.r * 3);
      nodeGlow.addColorStop(0, node.color + '30');
      nodeGlow.addColorStop(1, node.color + '00');
      ctx.globalAlpha = pulse * 0.5 * node.spawnProgress;
      ctx.fillStyle = nodeGlow;
      ctx.beginPath();
      ctx.arc(nx, ny, node.r * 3, 0, Math.PI * 2);
      ctx.fill();

      // Node body
      ctx.globalAlpha = (0.85 + pulse * 0.15) * node.spawnProgress;
      ctx.beginPath();
      ctx.arc(nx, ny, node.r, 0, Math.PI * 2);
      ctx.fillStyle = node.status === 'complete' ? '#22C55E' : node.color;
      ctx.fill();

      // Status ring
      if (node.status === 'working' && !reducedMotion) {
        ctx.beginPath();
        ctx.arc(nx, ny, node.r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = node.color;
        ctx.globalAlpha = 0.2 * pulse;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // Node label
      ctx.globalAlpha = node.spawnProgress;
      ctx.font = `bold ${isMobile ? 8 : 9}px 'JetBrains Mono', monospace`;
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.label, nx, ny);

      // Randomly emit data packets
      if (!reducedMotion && Math.random() < 0.003 && node.status === 'working') {
        const toHub = Math.random() > 0.5;
        addPacket(
          toHub ? nx : HUB.x, toHub ? ny : HUB.y,
          toHub ? HUB.x : nx, toHub ? HUB.y : ny,
          node.color
        );
      }
    }

    // === Data packets ===
    for (let i = dataPackets.length - 1; i >= 0; i--) {
      const p = dataPackets[i];
      p.progress += p.speed;
      if (p.progress >= 1) { dataPackets.splice(i, 1); continue; }

      const px = p.fromX + (p.toX - p.fromX) * p.progress;
      const py = p.fromY + (p.toY - p.fromY) * p.progress;
      const alpha = p.progress < 0.1 ? p.progress / 0.1
        : p.progress > 0.9 ? (1 - p.progress) / 0.1 : 1;

      // Packet glow
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fillStyle = p.color;
      ctx.globalAlpha = alpha * 0.3;
      ctx.fill();

      // Packet core
      ctx.beginPath();
      ctx.arc(px, py, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = '#fff';
      ctx.globalAlpha = alpha * 0.9;
      ctx.fill();
    }

    // === Spawn timer ===
    if (!reducedMotion) {
      spawnTimer += 16;
      if (spawnTimer > 6000 && nodes.length < NODE_COUNT) {
        spawnNode();
        spawnTimer = 0;
      }
    }

    ctx.globalAlpha = 1;
    animId = requestAnimationFrame(draw);
  }

  // Intersection observer — only animate when visible
  const observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) {
      if (!animId) draw();
    } else {
      if (animId) { cancelAnimationFrame(animId); animId = null; }
    }
  }, { threshold: 0 });

  resize();

  if (reducedMotion) {
    // Static: create all nodes at final positions, no animation
    initNodes();
    nodes.forEach(n => { n.spawnProgress = 1; });
    draw();
    cancelAnimationFrame(animId);
    animId = null;
  } else {
    // Animated: start with 0 nodes, spawn them in
    nodes = [];
    for (let i = 0; i < NODE_COUNT; i++) {
      setTimeout(() => spawnNode(), 400 + i * 300);
    }
    observer.observe(canvas);
  }

  // Mouse parallax
  if (!isMobile) {
    canvas.parentElement.addEventListener('mousemove', (e) => {
      const rect = canvas.parentElement.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = e.clientY - rect.top;
    });
    canvas.parentElement.addEventListener('mouseleave', () => {
      mouseX = -1; mouseY = -1;
    });
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { resize(); initNodes(); }, 200);
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
