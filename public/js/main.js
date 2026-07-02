/**
 * Orient Surprises Travel — Main JavaScript
 * Handles: navigation, scroll effects, accordion, animations, gallery
 */

'use strict';

/* ============================================================
   UTILITIES
   ============================================================ */

function debounce(fn, delay) {
  var timer;
  return function() {
    var args = arguments;
    var ctx = this;
    clearTimeout(timer);
    timer = setTimeout(function() { fn.apply(ctx, args); }, delay);
  };
}

function throttle(fn, limit) {
  var lastRun = 0;
  return function() {
    var now = Date.now();
    if (now - lastRun >= limit) {
      lastRun = now;
      fn.apply(this, arguments);
    }
  };
}

var $ = function(selector, ctx) {
  return (ctx || document).querySelector(selector);
};

var $$ = function(selector, ctx) {
  return Array.from((ctx || document).querySelectorAll(selector));
};

/* ============================================================
   NAVIGATION
   ============================================================ */

var Navigation = (function() {
  var nav, hamburger, mobileMenu, navLinks, sections;
  var menuOpen = false;

  function onScroll() {
    if (!nav) return;
    nav.classList.toggle('scrolled', window.scrollY > 60);
  }

  function toggleMenu(force) {
    menuOpen = typeof force === 'boolean' ? force : !menuOpen;
    if (hamburger) {
      hamburger.classList.toggle('open', menuOpen);
      hamburger.setAttribute('aria-expanded', String(menuOpen));
    }
    if (mobileMenu) mobileMenu.classList.toggle('open', menuOpen);
    document.body.style.overflow = menuOpen ? 'hidden' : '';
  }

  function handleNavLinkClick(e) {
    var href = e.currentTarget.getAttribute('href');
    if (!href || href.charAt(0) !== '#') return;
    e.preventDefault();
    var target = document.getElementById(href.slice(1));
    if (!target) return;
    var navHeight = nav ? nav.offsetHeight : 0;
    var top = target.getBoundingClientRect().top + window.scrollY - navHeight - 10;
    window.scrollTo({ top: top, behavior: 'smooth' });
    if (menuOpen) toggleMenu(false);
  }

  function highlightActiveLink() {
    if (!sections || !sections.length) return;
    var scrollY = window.scrollY + 120;
    var currentId = '';
    sections.forEach(function(section) {
      if (section.offsetTop <= scrollY) currentId = section.id;
    });
    if (navLinks) {
      navLinks.forEach(function(link) {
        var href = link.getAttribute('href') || '';
        link.classList.toggle('active', href === '#' + currentId);
      });
    }
  }

  function init() {
    nav        = $('.nav');
    hamburger  = $('.nav__hamburger');
    mobileMenu = $('.nav__mobile-menu');
    navLinks   = $$('.nav__link, .nav__mobile-link');
    sections   = $$('section[id]');

    if (!nav) return;

    onScroll();

    window.addEventListener('scroll', throttle(function() {
      onScroll();
      highlightActiveLink();
    }, 100));

    if (hamburger) {
      hamburger.addEventListener('click', function() { toggleMenu(); });
      hamburger.setAttribute('aria-expanded', 'false');
      hamburger.setAttribute('aria-label', 'Toggle navigation menu');
    }

    document.addEventListener('click', function(e) {
      if (menuOpen && nav && !nav.contains(e.target)) toggleMenu(false);
    });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && menuOpen) toggleMenu(false);
    });

    navLinks.forEach(function(link) {
      link.addEventListener('click', handleNavLinkClick);
    });

    var scrollIndicator = $('.hero__scroll-indicator');
    if (scrollIndicator) {
      scrollIndicator.addEventListener('click', function() {
        var trustBar = document.getElementById('trust-bar') || $('section:nth-of-type(2)');
        if (trustBar) {
          var navHeight = nav ? nav.offsetHeight : 0;
          var top = trustBar.getBoundingClientRect().top + window.scrollY - navHeight;
          window.scrollTo({ top: top, behavior: 'smooth' });
        }
      });
    }
  }

  return { init: init };
}());

/* ============================================================
   SCROLL ANIMATIONS — Intersection Observer
   ============================================================ */

var ScrollAnimations = (function() {
  var observer;

  function init() {
    var elements = $$('.fade-in, .fade-in-left, .fade-in-right');

    if (!('IntersectionObserver' in window)) {
      elements.forEach(function(el) { el.classList.add('visible'); });
      return;
    }

    observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

    elements.forEach(function(el) { observer.observe(el); });
  }

  function observeNew(elements) {
    if (!observer) return;
    elements.forEach(function(el) { observer.observe(el); });
  }

  return { init: init, observeNew: observeNew };
}());

/* ============================================================
   HERO — parallax & loaded class
   ============================================================ */

var Hero = (function() {
  function init() {
    var hero = $('.hero');
    if (!hero) return;

    requestAnimationFrame(function() { hero.classList.add('loaded'); });

    var heroBg = hero.querySelector('.hero__bg');
    if (!heroBg) return;

    var parallax = throttle(function() {
      var scrollY = window.scrollY;
      if (scrollY < window.innerHeight) {
        heroBg.style.transform = 'translateY(' + (scrollY * 0.3) + 'px) scale(1)';
      }
    }, 16);

    window.addEventListener('scroll', parallax, { passive: true });
  }

  return { init: init };
}());

/* ============================================================
   COUNTER ANIMATION — trust bar stats
   ============================================================ */

var CounterAnimation = (function() {
  function animateCounter(el, target, duration, suffix) {
    var start   = performance.now();
    var isFloat = !Number.isInteger(target);

    function step(timestamp) {
      var elapsed  = timestamp - start;
      var progress = Math.min(elapsed / duration, 1);
      var eased    = 1 - Math.pow(1 - progress, 3);
      var current  = eased * target;

      el.textContent = isFloat
        ? current.toFixed(1) + suffix
        : Math.round(current).toLocaleString() + suffix;

      if (progress < 1) requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
  }

  function init() {
    var statNumbers = $$('.trust-bar__number');
    if (!statNumbers.length || !('IntersectionObserver' in window)) return;

    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (!entry.isIntersecting) return;
        var el    = entry.target;
        var raw   = el.dataset.value || el.textContent.trim();
        var match = raw.match(/^([0-9.]+)(.*)$/);
        if (!match) return;
        var target = parseFloat(match[1]);
        var suffix = match[2] || '';
        animateCounter(el, target, 1800, suffix);
        observer.unobserve(el);
      });
    }, { threshold: 0.5 });

    statNumbers.forEach(function(el) {
      if (!el.dataset.value) el.dataset.value = el.textContent.trim();
      observer.observe(el);
    });
  }

  return { init: init };
}());

/* ============================================================
   TOUR CARD ACCORDION — itinerary toggle
   ============================================================ */

var TourAccordion = (function() {
  function init() {
    var toggles = $$('.tour-card__accordion-toggle');
    if (!toggles.length) return;

    toggles.forEach(function(toggle) {
      var itinerary = toggle.nextElementSibling;
      if (!itinerary) return;

      var id = 'itinerary-' + Math.random().toString(36).slice(2, 8);
      itinerary.id = id;
      toggle.setAttribute('aria-controls', id);
      toggle.setAttribute('aria-expanded', 'false');

      toggle.addEventListener('click', function() {
        var isOpen = toggle.classList.toggle('open');
        itinerary.classList.toggle('open', isOpen);
        toggle.setAttribute('aria-expanded', String(isOpen));
      });
    });
  }

  return { init: init };
}());

/* ============================================================
   GALLERY LIGHTBOX
   ============================================================ */

var Gallery = (function() {
  var lightbox, lightboxImg, lightboxCaption, lightboxClose;
  var currentItems = [];
  var currentIndex = 0;

  function createLightbox() {
    lightbox = document.createElement('div');
    lightbox.setAttribute('role', 'dialog');
    lightbox.setAttribute('aria-modal', 'true');
    lightbox.setAttribute('aria-label', 'Image lightbox');
    lightbox.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(4,30,69,0.95);z-index:9999;align-items:center;justify-content:center;padding:1rem;';

    var btnBase = 'width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;';

    lightbox.innerHTML =
      '<button class="lightbox__close" aria-label="Close" style="position:absolute;top:1.25rem;right:1.25rem;' + btnBase + 'font-size:1.25rem;">&#x2715;</button>' +
      '<button class="lightbox__prev" aria-label="Previous" style="position:absolute;left:1.25rem;top:50%;transform:translateY(-50%);' + btnBase + 'font-size:1.5rem;">&#x2039;</button>' +
      '<div style="text-align:center;max-width:90vw;max-height:90vh;">' +
        '<img class="lightbox__img" src="" alt="" style="max-width:90vw;max-height:80vh;object-fit:contain;border-radius:8px;display:block;margin:0 auto;">' +
        '<p class="lightbox__caption" style="color:rgba(248,245,238,0.75);font-size:0.9rem;margin-top:0.75rem;font-family:Inter,sans-serif;"></p>' +
      '</div>' +
      '<button class="lightbox__next" aria-label="Next" style="position:absolute;right:1.25rem;top:50%;transform:translateY(-50%);' + btnBase + 'font-size:1.5rem;">&#x203A;</button>';

    document.body.appendChild(lightbox);

    lightboxImg     = lightbox.querySelector('.lightbox__img');
    lightboxCaption = lightbox.querySelector('.lightbox__caption');
    lightboxClose   = lightbox.querySelector('.lightbox__close');

    lightboxClose.addEventListener('click', close);
    lightbox.querySelector('.lightbox__prev').addEventListener('click', function() { navigate(-1); });
    lightbox.querySelector('.lightbox__next').addEventListener('click', function() { navigate(1); });
    lightbox.addEventListener('click', function(e) { if (e.target === lightbox) close(); });
  }

  function open(index) {
    currentIndex = index;
    show();
    lightbox.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    lightboxClose.focus();
  }

  function close() {
    lightbox.style.display = 'none';
    document.body.style.overflow = '';
  }

  function navigate(dir) {
    currentIndex = (currentIndex + dir + currentItems.length) % currentItems.length;
    show();
  }

  function show() {
    var item = currentItems[currentIndex];
    if (!item) return;
    lightboxImg.src = item.src;
    lightboxImg.alt = item.caption;
    lightboxCaption.textContent = item.caption;
  }

  function init() {
    var galleryItems = $$('.gallery__item');
    if (!galleryItems.length) return;

    createLightbox();

    currentItems = galleryItems.map(function(item) {
      var img     = item.querySelector('img');
      var caption = item.querySelector('.gallery__item-caption');
      return {
        src:     img     ? img.src             : '',
        caption: caption ? caption.textContent : ''
      };
    });

    galleryItems.forEach(function(item, i) {
      item.setAttribute('tabindex', '0');
      item.setAttribute('role', 'button');
      item.setAttribute('aria-label', 'View image ' + (i + 1));
      item.addEventListener('click', function() { open(i); });
      item.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(i); }
      });
    });

    document.addEventListener('keydown', function(e) {
      if (lightbox.style.display !== 'flex') return;
      if (e.key === 'Escape')     close();
      if (e.key === 'ArrowLeft')  navigate(-1);
      if (e.key === 'ArrowRight') navigate(1);
    });
  }

  return { init: init };
}());

/* ============================================================
   TEAM CARDS — keyboard-accessible bio reveal
   ============================================================ */

var TeamCards = (function() {
  function init() {
    $$('.team-card').forEach(function(card) {
      card.setAttribute('tabindex', '0');
      card.addEventListener('focusin',  function() { card.classList.add('focused'); });
      card.addEventListener('focusout', function() { card.classList.remove('focused'); });
    });
  }

  return { init: init };
}());

/* ============================================================
   READING PROGRESS BAR
   ============================================================ */

var ReadingProgress = (function() {
  function init() {
    var bar = document.createElement('div');
    bar.setAttribute('aria-hidden', 'true');
    bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;width:0%;background:linear-gradient(90deg,#B8860B,#C69214);z-index:9998;transition:width 0.1s linear;pointer-events:none;';
    document.body.appendChild(bar);

    window.addEventListener('scroll', throttle(function() {
      var docHeight = document.documentElement.scrollHeight - window.innerHeight;
      var scrolled  = docHeight > 0 ? (window.scrollY / docHeight) * 100 : 0;
      bar.style.width = Math.min(scrolled, 100) + '%';
    }, 16), { passive: true });
  }

  return { init: init };
}());

/* ============================================================
   CONTACT FORM — validation & UX
   ============================================================ */

var ContactForm = (function() {
  function validateEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  function showMessage(form, msg, type) {
    var el = form.querySelector('.form-message');
    if (!el) {
      el = document.createElement('p');
      el.className = 'form-message';
      form.appendChild(el);
    }
    var ok = type === 'success';
    el.textContent = msg;
    el.style.cssText =
      'margin-top:1rem;padding:0.75rem 1rem;border-radius:6px;font-size:0.9rem;font-family:Inter,sans-serif;' +
      'background:' + (ok ? 'rgba(6,42,94,0.08)'  : 'rgba(200,30,30,0.08)') + ';' +
      'color:'      + (ok ? '#062A5E'              : '#9b1c1c')              + ';' +
      'border:1px solid ' + (ok ? 'rgba(6,42,94,0.2)' : 'rgba(200,30,30,0.2)') + ';';
  }

  function init() {
    $$('form[data-contact]').forEach(function(form) {
      form.addEventListener('submit', function(e) {
        e.preventDefault();
        var nameEl  = form.querySelector('[name="name"]');
        var emailEl = form.querySelector('[name="email"]');
        var msgEl   = form.querySelector('[name="message"]');

        if (nameEl && !nameEl.value.trim()) {
          showMessage(form, 'Please enter your name.', 'error');
          nameEl.focus(); return;
        }
        if (emailEl && !validateEmail(emailEl.value.trim())) {
          showMessage(form, 'Please enter a valid email address.', 'error');
          emailEl.focus(); return;
        }
        if (msgEl && !msgEl.value.trim()) {
          showMessage(form, 'Please tell us about your travel plans.', 'error');
          msgEl.focus(); return;
        }

        var submitBtn = form.querySelector('[type="submit"]');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Sending…'; }

        /* Replace setTimeout with real fetch() to your backend endpoint */
        setTimeout(function() {
          showMessage(form, 'Thank you! We will be in touch within 24 hours.', 'success');
          form.reset();
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Send Message'; }
        }, 1200);
      });
    });
  }

  return { init: init };
}());

/* ============================================================
   LAZY IMAGE LOADING
   ============================================================ */

var LazyImages = (function() {
  function init() {
    if (!('IntersectionObserver' in window)) return;
    var imgs = $$('img[data-src]');
    if (!imgs.length) return;

    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (!entry.isIntersecting) return;
        var img = entry.target;
        img.src = img.dataset.src;
        if (img.dataset.srcset) img.srcset = img.dataset.srcset;
        img.removeAttribute('data-src');
        img.removeAttribute('data-srcset');
        observer.unobserve(img);
      });
    }, { rootMargin: '200px 0px' });

    imgs.forEach(function(img) { observer.observe(img); });
  }

  return { init: init };
}());

/* ============================================================
   BACK TO TOP BUTTON
   ============================================================ */

var BackToTop = (function() {
  function init() {
    var btn = document.createElement('button');
    btn.innerHTML = '&#8679;';
    btn.setAttribute('aria-label', 'Back to top');
    btn.style.cssText =
      'position:fixed;bottom:2rem;right:2rem;width:48px;height:48px;border-radius:50%;' +
      'background:linear-gradient(135deg,#C69214,#B8860B);color:#062A5E;font-size:1.5rem;' +
      'font-weight:700;border:none;cursor:pointer;box-shadow:0 4px 20px rgba(184,134,11,0.4);' +
      'display:flex;align-items:center;justify-content:center;opacity:0;' +
      'transform:translateY(20px);transition:opacity 0.3s ease,transform 0.3s ease;' +
      'z-index:500;pointer-events:none;';
    document.body.appendChild(btn);

    window.addEventListener('scroll', throttle(function() {
      var show = window.scrollY > 400;
      btn.style.opacity       = show ? '1' : '0';
      btn.style.transform     = show ? 'translateY(0)' : 'translateY(20px)';
      btn.style.pointerEvents = show ? 'auto' : 'none';
    }, 200), { passive: true });

    btn.addEventListener('click', function() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  return { init: init };
}());

/* ============================================================
   STICKY SECTION HIGHLIGHT (nav link active state)
   ============================================================ */

var StickyHighlight = (function() {
  function init() {
    var sections = $$('section[id]');
    if (!sections.length || !('IntersectionObserver' in window)) return;

    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (!entry.isIntersecting) return;
        $$('.nav__link').forEach(function(l) { l.classList.remove('active'); });
        var link = $('.nav__link[href="#' + entry.target.id + '"]');
        if (link) link.classList.add('active');
      });
    }, { rootMargin: '-40% 0px -55% 0px' });

    sections.forEach(function(s) { observer.observe(s); });
  }

  return { init: init };
}());

/* ============================================================
   TOUR FILTER — data-filter-btn / data-type attributes
   ============================================================ */

var TourFilter = (function() {
  function init() {
    var filterBtns = $$('[data-filter-btn]');
    var tourCards  = $$('.tour-card');
    if (!filterBtns.length || !tourCards.length) return;

    filterBtns.forEach(function(btn) {
      btn.addEventListener('click', function() {
        var filter = btn.dataset.filterBtn;
        filterBtns.forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        tourCards.forEach(function(card) {
          card.style.display = (filter === 'all' || card.dataset.type === filter) ? '' : 'none';
        });
      });
    });
  }

  return { init: init };
}());

/* ============================================================
   TESTIMONIALS — simple auto-rotate on mobile viewports
   ============================================================ */

var TestimonialsCarousel = (function() {
  var interval;

  function init() {
    var grid = $('.testimonials__grid');
    if (!grid) return;
    var cards = $$('.testimonial-card', grid);
    if (cards.length < 2) return;

    function check() {
      if (window.innerWidth > 768) {
        clearInterval(interval);
        cards.forEach(function(c) { c.style.display = ''; });
        return;
      }
      cards.forEach(function(c, i) { c.style.display = i === 0 ? '' : 'none'; });
      var current = 0;
      clearInterval(interval);
      interval = setInterval(function() {
        cards[current].style.display = 'none';
        current = (current + 1) % cards.length;
        cards[current].style.display = '';
      }, 4000);
    }

    check();
    window.addEventListener('resize', debounce(check, 300));
  }

  return { init: init };
}());

/* ============================================================
   SMOOTH ANCHOR LINKS — all in-page hrefs
   ============================================================ */

function initSmoothLinks() {
  $$('a[href^="#"]').forEach(function(anchor) {
    anchor.addEventListener('click', function(e) {
      var href = anchor.getAttribute('href');
      if (href === '#') return;
      var target = document.getElementById(href.slice(1));
      if (!target) return;
      e.preventDefault();
      var nav    = $('.nav');
      var offset = nav ? nav.offsetHeight + 10 : 10;
      var top    = target.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top: top, behavior: 'smooth' });
    });
  });
}

/* ============================================================
   STAT NUMBER PREP — cache data-value before animation
   ============================================================ */

function formatStatNumbers() {
  $$('.trust-bar__number').forEach(function(el) {
    if (!el.dataset.value) el.dataset.value = el.textContent.trim();
  });
}

/* ============================================================
   INIT — DOMContentLoaded
   ============================================================ */

document.addEventListener('DOMContentLoaded', function() {
  formatStatNumbers();
  Navigation.init();
  ScrollAnimations.init();
  Hero.init();
  CounterAnimation.init();
  TourAccordion.init();
  Gallery.init();
  TeamCards.init();
  ReadingProgress.init();
  ContactForm.init();
  LazyImages.init();
  BackToTop.init();
  StickyHighlight.init();
  TourFilter.init();
  TestimonialsCarousel.init();
  initSmoothLinks();

  /* Signal to CSS (js-loaded class can reveal content, prevent FOUC) */
  document.documentElement.classList.add('js-loaded');
});

/* ============================================================
   MODULE EXPORT (bundler / Node test environments)
   ============================================================ */

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    Navigation:            Navigation,
    ScrollAnimations:      ScrollAnimations,
    Hero:                  Hero,
    CounterAnimation:      CounterAnimation,
    TourAccordion:         TourAccordion,
    Gallery:               Gallery,
    TeamCards:             TeamCards,
    ReadingProgress:       ReadingProgress,
    ContactForm:           ContactForm,
    LazyImages:            LazyImages,
    BackToTop:             BackToTop,
    StickyHighlight:       StickyHighlight,
    TourFilter:            TourFilter,
    TestimonialsCarousel:  TestimonialsCarousel
  };
}
