/**
 * ThemisIQ — Particle Network Animation
 * Self-contained IIFE. Auto-starts on any page that contains:
 *   <canvas id="aegis-particles" aria-hidden="true"></canvas>
 *
 * Colour is read from the CSS custom property --particle-color-rgb (RGB triplet string).
 * Respects prefers-reduced-motion. Pauses when the tab is hidden.
 */
(function () {
  'use strict';

  // ── Accessibility: skip animation if user prefers reduced motion ─────────
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var canvas = document.getElementById('aegis-particles');
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext('2d');
  var raf = null;
  var particles = [];
  var mouse = { x: null, y: null, radius: 120 };
  var _rgb = null;  // Cached theme RGB string — invalidated on module/theme changes

  // ── Read current module theme colour ────────────────────────────────────
  function getRgb() {
    if (!_rgb) {
      var val = (getComputedStyle(document.documentElement)
                   .getPropertyValue('--particle-color-rgb') || '').trim();
      _rgb = val || '100,140,200';
    }
    return _rgb;
  }

  // Invalidate cache when the module or theme class changes
  if (window.MutationObserver) {
    new MutationObserver(function () { _rgb = null; })
      .observe(document.body, { attributes: true, attributeFilter: ['data-module', 'class', 'data-theme'] });
  }

  // ── Particle constructor ─────────────────────────────────────────────────
  function Particle() {
    this.x  = Math.random() * canvas.width;
    this.y  = Math.random() * canvas.height;
    this.vx = (Math.random() * 0.3) - 0.15;   // Very slow: ±0.15 px/frame
    this.vy = (Math.random() * 0.3) - 0.15;
    this.sz = Math.random() * 1.2 + 0.4;       // 0.4–1.6 px — deliberately tiny
  }

  Particle.prototype.update = function () {
    // Bounce at viewport edges
    if (this.x > canvas.width  || this.x < 0) this.vx = -this.vx;
    if (this.y > canvas.height || this.y < 0) this.vy = -this.vy;
    // Gentle mouse repel
    if (mouse.x !== null) {
      var dx = mouse.x - this.x, dy = mouse.y - this.y;
      var d  = Math.sqrt(dx * dx + dy * dy);
      if (d < mouse.radius && d > 0) {
        var force = (mouse.radius - d) / mouse.radius;
        this.x -= (dx / d) * force * 2;
        this.y -= (dy / d) * force * 2;
      }
    }
    this.x += this.vx;
    this.y += this.vy;
  };

  Particle.prototype.draw = function (rgb) {
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.sz, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(' + rgb + ',0.8)';
    ctx.fill();
  };

  // ── Initialise particle array ────────────────────────────────────────────
  function init() {
    particles = [];
    // Cap at 70 particles regardless of screen size — performance safety
    var n = Math.min(70, Math.floor((canvas.width * canvas.height) / 13000));
    for (var i = 0; i < n; i++) particles.push(new Particle());
  }

  // ── Draw connection lines between nearby particles ───────────────────────
  function connect(rgb) {
    var maxD  = Math.min(canvas.width / 6.5, 150);  // Adaptive: ~150px max
    var maxD2 = maxD * maxD;
    for (var a = 0; a < particles.length; a++) {
      for (var b = a + 1; b < particles.length; b++) {
        var dx = particles[a].x - particles[b].x;
        var dy = particles[a].y - particles[b].y;
        var d2 = dx * dx + dy * dy;
        if (d2 < maxD2) {
          // Opacity fades to zero at maxD; stays well below 0.10 — very subtle
          var op = (1 - d2 / maxD2) * 0.32;
          ctx.strokeStyle = 'rgba(' + rgb + ',' + op + ')';
          ctx.lineWidth   = 0.6;
          ctx.beginPath();
          ctx.moveTo(particles[a].x, particles[a].y);
          ctx.lineTo(particles[b].x, particles[b].y);
          ctx.stroke();
        }
      }
    }
  }

  // ── Main animation loop ──────────────────────────────────────────────────
  function animate() {
    raf = requestAnimationFrame(animate);
    ctx.clearRect(0, 0, canvas.width, canvas.height);  // Transparent — page bg shows through
    var rgb = getRgb();
    for (var i = 0; i < particles.length; i++) particles[i].update();
    for (var i = 0; i < particles.length; i++) particles[i].draw(rgb);
    connect(rgb);
  }

  // ── Resize handler ───────────────────────────────────────────────────────
  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
    init();
  }

  // ── Pause when tab is hidden (saves CPU & battery) ───────────────────────
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      cancelAnimationFrame(raf);
    } else {
      animate();
    }
  });

  // ── Wire up events ───────────────────────────────────────────────────────
  window.addEventListener('resize', resize, { passive: true });
  document.addEventListener('mousemove', function (e) {
    mouse.x = e.clientX; mouse.y = e.clientY;
  }, { passive: true });
  document.addEventListener('mouseleave', function () {
    mouse.x = null; mouse.y = null;
  }, { passive: true });

  // ── Start ────────────────────────────────────────────────────────────────
  resize();
  animate();
}());
