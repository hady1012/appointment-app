(function () {
    const themeStorageKey = 'marketplace-theme';
    const accessibilityStorageKey = 'marketplace-accessibility';
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const savedTheme = localStorage.getItem(themeStorageKey);
    const initialTheme = savedTheme || (prefersDark ? 'dark' : 'light');
    const root = document.documentElement;
    const defaults = {
        readableFont: false,
        largerText: false,
        highContrast: false,
        highlightLinks: false,
        highlightHeadings: false,
        readingGuide: false,
        reduceMotion: false,
        launcherPosition: 'bottom-left',
        launcherCustomLeft: null,
        launcherCustomTop: null
    };

    function readSettings() {
        try {
            return { ...defaults, ...JSON.parse(localStorage.getItem(accessibilityStorageKey) || '{}') };
        } catch (error) {
            return { ...defaults };
        }
    }

    function saveSettings(settings) {
        localStorage.setItem(accessibilityStorageKey, JSON.stringify(settings));
    }

    function normalizeLauncherPosition(settings) {
        return {
            left: 'bottom-left',
            right: 'bottom-right'
        }[settings.launcherPosition] || settings.launcherPosition || 'bottom-left';
    }

    function applyTheme(theme) {
        root.dataset.theme = theme;
        localStorage.setItem(themeStorageKey, theme);
        document.querySelectorAll('[data-theme-toggle]').forEach((toggle) => {
            const isDark = theme === 'dark';
            toggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
            toggle.classList.toggle('is-active', isDark);
            const label = toggle.querySelector('[data-option-state]');
            if (label) {
                label.textContent = isDark ? 'On' : 'Off';
            }
        });
    }

    function applySettings(settings) {
        root.classList.toggle('a11y-readable-font', settings.readableFont);
        root.classList.toggle('a11y-larger-text', settings.largerText);
        root.classList.toggle('a11y-high-contrast', settings.highContrast);
        root.classList.toggle('a11y-highlight-links', settings.highlightLinks);
        root.classList.toggle('a11y-highlight-headings', settings.highlightHeadings);
        root.classList.toggle('a11y-reading-guide-on', settings.readingGuide);
        root.classList.toggle('a11y-reduce-motion', settings.reduceMotion);

        Object.keys(defaults).forEach((key) => {
            document.querySelectorAll(`[data-a11y-setting="${key}"]`).forEach((button) => {
                const enabled = Boolean(settings[key]);
                button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
                button.classList.toggle('is-active', enabled);
                const label = button.querySelector('[data-option-state]');
                if (label) {
                    label.textContent = enabled ? 'On' : 'Off';
                }
            });
        });

        document.querySelectorAll('[data-a11y-position]').forEach((button) => {
            const normalizedPosition = normalizeLauncherPosition(settings);
            const enabled = button.dataset.a11yPosition === normalizedPosition;
            button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            button.classList.toggle('is-active', enabled);
        });

        const widget = document.querySelector('[data-accessibility-widget]');
        if (widget) {
            if (Number.isFinite(settings.launcherCustomLeft) && Number.isFinite(settings.launcherCustomTop)) {
                widget.dataset.position = 'custom';
                widget.style.left = `${settings.launcherCustomLeft}px`;
                widget.style.top = `${settings.launcherCustomTop}px`;
                widget.style.right = 'auto';
                widget.style.bottom = 'auto';
            } else {
                widget.dataset.position = normalizeLauncherPosition(settings);
                widget.style.left = '';
                widget.style.top = '';
                widget.style.right = '';
                widget.style.bottom = '';
            }
        }
    }

    function option(icon, title, setting, detail) {
        return [
            `<button type="button" class="accessibility-option" data-a11y-setting="${setting}" aria-pressed="false">`,
            `<span class="accessibility-icon" aria-hidden="true">${icon}</span>`,
            '<span class="accessibility-copy">',
            `<strong>${title}</strong>`,
            `<small>${detail}</small>`,
            '</span>',
            '<em data-option-state>Off</em>',
            '</button>'
        ].join('');
    }

    function buildWidget() {
        if (document.querySelector('[data-accessibility-widget]')) {
            return;
        }

        const widget = document.createElement('div');
        widget.className = 'accessibility-widget';
        widget.dataset.accessibilityWidget = 'true';
        widget.innerHTML = [
            '<button type="button" class="accessibility-launcher" data-accessibility-toggle aria-label="Accessibility menu" title="Accessibility" aria-expanded="false" aria-controls="accessibility-panel">',
            '<span class="accessibility-person-icon" aria-hidden="true">',
            '<svg viewBox="0 0 64 64" focusable="false">',
            '<circle cx="32" cy="32" r="29"></circle>',
            '<circle cx="32" cy="17" r="5"></circle>',
            '<path d="M17 25c9 3 21 3 30 0"></path>',
            '<path d="M32 23v17"></path>',
            '<path d="M27 40l-6 15"></path>',
            '<path d="M37 40l6 15"></path>',
            '</svg>',
            '</span>',
            '<b>Accessibility</b>',
            '</button>',
            '<section class="accessibility-panel" id="accessibility-panel" aria-label="Accessibility options">',
            '<div class="accessibility-panel-title">',
            '<button type="button" class="accessibility-close" data-accessibility-close aria-label="Close accessibility menu">x</button>',
            '<h2>Accessibility</h2>',
            '<span></span>',
            '</div>',
            '<div class="accessibility-position" aria-label="Accessibility button position">',
            '<strong>Move accessibility button</strong>',
            '<div class="accessibility-position-buttons">',
            '<button type="button" data-a11y-position="bottom-left" aria-pressed="true">Bottom left</button>',
            '<button type="button" data-a11y-position="bottom-right" aria-pressed="false">Bottom right</button>',
            '<button type="button" data-a11y-position="top-left" aria-pressed="false">Top left</button>',
            '<button type="button" data-a11y-position="top-right" aria-pressed="false">Top right</button>',
            '</div>',
            '</div>',
            '<div class="accessibility-options">',
            '<button type="button" class="accessibility-option" data-theme-toggle aria-pressed="false">',
            '<span class="accessibility-icon" aria-hidden="true">DM</span>',
            '<span class="accessibility-copy"><strong>Dark mode</strong><small>Switch the site colors</small></span>',
            '<em data-option-state>Off</em>',
            '</button>',
            option('Aa', 'Readable font', 'readableFont', 'Cleaner letter shapes'),
            option('A+', 'Text magnifier', 'largerText', 'Increase text size'),
            option('HC', 'High contrast', 'highContrast', 'Sharper page colors'),
            option('HL', 'Highlight links', 'highlightLinks', 'Underline every link'),
            option('HH', 'Highlight headers', 'highlightHeadings', 'Frame page headings'),
            option('RG', 'Reading guide', 'readingGuide', 'Follow the cursor'),
            option('RM', 'Reduce motion', 'reduceMotion', 'Quiet animations'),
            '</div>',
            '<button type="button" class="accessibility-reset" data-accessibility-reset>Reset accessibility</button>',
            '</section>',
            '<div class="accessibility-reading-guide" data-reading-guide aria-hidden="true"></div>'
        ].join('');

        document.body.appendChild(widget);
    }

    function closeMenu() {
        const widget = document.querySelector('[data-accessibility-widget]');
        const toggle = document.querySelector('[data-accessibility-toggle]');
        widget?.classList.remove('is-open');
        toggle?.setAttribute('aria-expanded', 'false');
    }

    function closeNavMenu() {
        const navbar = document.querySelector('.navbar');
        const menuButton = document.querySelector('[data-nav-more]');
        navbar?.classList.remove('nav-menu-open');
        menuButton?.setAttribute('aria-expanded', 'false');
    }

    function isPlainLeftClick(event) {
        return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    }

    function getSamePageLink(target) {
        const link = target.closest?.('a[href]');
        if (!link || link.target || link.hasAttribute('download') || link.dataset.noInstantNav === 'true') {
            return null;
        }

        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin || url.protocol !== window.location.protocol) {
            return null;
        }

        if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) {
            return null;
        }

        return { link, url };
    }

    function installFastPageActions() {
        document.addEventListener('pointerdown', (event) => {
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return;
            }

            const match = getSamePageLink(event.target);
            if (match) {
                match.link.classList.add('is-pressing-link');
            }
        }, { capture: true, passive: true });

        document.addEventListener('pointerup', () => {
            document.querySelectorAll('.is-pressing-link').forEach((link) => {
                link.classList.remove('is-pressing-link');
            });
        }, { capture: true, passive: true });

        document.addEventListener('click', (event) => {
            if (!isPlainLeftClick(event) || event.defaultPrevented) {
                return;
            }

            const match = getSamePageLink(event.target);
            if (!match) {
                return;
            }

            event.preventDefault();
            match.link.setAttribute('aria-busy', 'true');
            match.link.classList.add('is-navigating-link');
            window.location.assign(match.url.href);
        }, { capture: true });
    }

    function iconSvg(name) {
        const icons = {
            search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"></circle><path d="M16 16l5 5"></path></svg>',
            account: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"></circle><path d="M4.5 21a7.5 7.5 0 0 1 15 0"></path></svg>',
            picks: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7Z"></path><path d="M9 7a3 3 0 0 1 6 0"></path></svg>',
            menu: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"></path><path d="M4 12h16"></path><path d="M4 17h16"></path></svg>'
        };
        return icons[name] || icons.menu;
    }

    function createNavAction(tag, options) {
        const element = document.createElement(tag);
        element.className = 'nav-icon-action';
        if (tag === 'a') {
            element.href = options.href;
        } else {
            element.type = 'button';
        }
        element.setAttribute('aria-label', options.label);
        element.title = options.label;
        element.innerHTML = iconSvg(options.icon);
        return element;
    }

    function enhanceNavigation() {
        const navbar = document.querySelector('.navbar');
        const originalLinks = navbar?.querySelector('.nav-links');
        const logo = navbar?.querySelector('.nav-logo');
        if (!navbar || !originalLinks || navbar.dataset.enhancedNav === 'true') {
            return;
        }

        navbar.dataset.enhancedNav = 'true';
        originalLinks.classList.add('nav-menu-panel');

        if (logo && logo.tagName.toLowerCase() !== 'a') {
            const logoLink = document.createElement('a');
            logoLink.className = `${logo.className} nav-logo-link`;
            logoLink.href = '/';
            logoLink.setAttribute('aria-label', 'Golan Pick home');
            while (logo.firstChild) {
                logoLink.appendChild(logo.firstChild);
            }
            logo.replaceWith(logoLink);
        }

        const links = Array.from(originalLinks.querySelectorAll('a[href]'));
        const isLoggedIn = links.some((link) => link.getAttribute('href')?.includes('/logout'));
        const isOwner = links.some((link) => link.getAttribute('href')?.includes('/work')) || window.location.pathname === '/work';
        const accountHref = isLoggedIn ? '/logout' : '/login';
        const accountLabel = isLoggedIn ? 'Log out' : 'Log in or sign up';
        const picksHref = isOwner ? '/work' : (isLoggedIn ? '/appointments' : '/login');
        const picksLabel = isOwner ? 'Today bookings' : 'My picked times';

        const quickActions = document.createElement('div');
        quickActions.className = 'nav-quick-actions';
        quickActions.appendChild(createNavAction('a', { href: '/pick', label: 'Search businesses', icon: 'search' }));
        quickActions.appendChild(createNavAction('a', { href: accountHref, label: accountLabel, icon: 'account' }));
        quickActions.appendChild(createNavAction('a', { href: picksHref, label: picksLabel, icon: 'picks' }));

        const menuButton = createNavAction('button', { label: 'Open menu', icon: 'menu' });
        menuButton.dataset.navMore = 'true';
        menuButton.setAttribute('aria-expanded', 'false');
        menuButton.addEventListener('click', (event) => {
            event.stopPropagation();
            const isOpen = !navbar.classList.contains('nav-menu-open');
            navbar.classList.toggle('nav-menu-open', isOpen);
            menuButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
        quickActions.appendChild(menuButton);

        navbar.appendChild(quickActions);

        let lastScrollY = window.scrollY;
        window.addEventListener('scroll', () => {
            const currentScrollY = window.scrollY;
            const shouldHide = currentScrollY > lastScrollY && currentScrollY > 80 && !navbar.classList.contains('nav-menu-open');
            navbar.classList.toggle('nav-hidden', shouldHide);
            if (!shouldHide && currentScrollY > 80) {
                navbar.classList.add('nav-scrolled');
            } else {
                navbar.classList.toggle('nav-scrolled', currentScrollY > 80);
            }
            lastScrollY = Math.max(0, currentScrollY);
        }, { passive: true });
    }

    function installLogoSplash() {
        if (sessionStorage.getItem('golan-logo-splash-seen') === 'true' || root.classList.contains('a11y-reduce-motion')) {
            return;
        }
        sessionStorage.setItem('golan-logo-splash-seen', 'true');
        const mark = document.querySelector('.nav-logo-mark')?.getAttribute('src') || '/static/golan-pick-mark.svg';
        const splash = document.createElement('div');
        splash.className = 'brand-splash';
        splash.innerHTML = [
            '<div class="brand-splash-inner">',
            `<img src="${mark}" alt="">`,
            '<strong>Golan Pick</strong>',
            '</div>'
        ].join('');
        document.body.appendChild(splash);
        setTimeout(() => {
            splash.classList.add('is-leaving');
            setTimeout(() => splash.remove(), 520);
        }, 1450);
    }

    function installAccessibilityDrag() {
        const widget = document.querySelector('[data-accessibility-widget]');
        const launcher = document.querySelector('[data-accessibility-toggle]');
        if (!widget || !launcher) return;

        let longPressTimer = null;
        let dragging = false;
        let dragOffsetX = 0;
        let dragOffsetY = 0;
        let suppressClick = false;

        const clearLongPress = () => {
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        };

        const moveWidget = (event) => {
            if (!dragging) return;
            const maxLeft = window.innerWidth - widget.offsetWidth - 8;
            const maxTop = window.innerHeight - widget.offsetHeight - 8;
            const left = Math.max(8, Math.min(maxLeft, event.clientX - dragOffsetX));
            const top = Math.max(8, Math.min(maxTop, event.clientY - dragOffsetY));
            widget.dataset.position = 'custom';
            widget.style.left = `${left}px`;
            widget.style.top = `${top}px`;
            widget.style.right = 'auto';
            widget.style.bottom = 'auto';
        };

        launcher.addEventListener('pointerdown', (event) => {
            if (event.button && event.button !== 0) return;
            clearLongPress();
            const rect = widget.getBoundingClientRect();
            dragOffsetX = event.clientX - rect.left;
            dragOffsetY = event.clientY - rect.top;
            longPressTimer = setTimeout(() => {
                dragging = true;
                suppressClick = true;
                widget.classList.add('is-dragging');
                closeMenu();
                launcher.setPointerCapture?.(event.pointerId);
            }, 280);
        });

        launcher.addEventListener('pointermove', moveWidget);

        launcher.addEventListener('pointerup', (event) => {
            clearLongPress();
            if (dragging) {
                dragging = false;
                widget.classList.remove('is-dragging');
                launcher.releasePointerCapture?.(event.pointerId);
                const rect = widget.getBoundingClientRect();
                const next = readSettings();
                next.launcherCustomLeft = Math.round(rect.left);
                next.launcherCustomTop = Math.round(rect.top);
                saveSettings(next);
                applySettings(next);
                setTimeout(() => {
                    suppressClick = false;
                }, 80);
            }
        });

        launcher.addEventListener('pointercancel', () => {
            clearLongPress();
            dragging = false;
            widget.classList.remove('is-dragging');
        });

        launcher.addEventListener('click', (event) => {
            if (suppressClick) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }, { capture: true });
    }

    applyTheme(initialTheme);
    applySettings(readSettings());

    document.addEventListener('DOMContentLoaded', () => {
        buildWidget();
        installFastPageActions();
        enhanceNavigation();
        applyTheme(root.dataset.theme || initialTheme);
        applySettings(readSettings());
        installAccessibilityDrag();
        installLogoSplash();

        document.querySelector('[data-accessibility-toggle]')?.addEventListener('click', (event) => {
            event.stopPropagation();
            const widget = document.querySelector('[data-accessibility-widget]');
            const isOpen = !widget.classList.contains('is-open');
            widget.classList.toggle('is-open', isOpen);
            event.currentTarget.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        document.querySelector('[data-accessibility-close]')?.addEventListener('click', closeMenu);

        document.querySelector('[data-theme-toggle]')?.addEventListener('click', () => {
            applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark');
        });

        document.querySelectorAll('[data-a11y-setting]').forEach((button) => {
            button.addEventListener('click', () => {
                const key = button.dataset.a11ySetting;
                const next = readSettings();
                next[key] = !next[key];
                saveSettings(next);
                applySettings(next);
            });
        });

        document.querySelectorAll('[data-a11y-position]').forEach((button) => {
            button.addEventListener('click', () => {
                const next = readSettings();
                next.launcherPosition = button.dataset.a11yPosition || 'bottom-left';
                next.launcherCustomLeft = null;
                next.launcherCustomTop = null;
                saveSettings(next);
                applySettings(next);
            });
        });

        document.querySelector('[data-accessibility-reset]')?.addEventListener('click', () => {
            saveSettings({ ...defaults });
            applySettings({ ...defaults });
        });

        document.addEventListener('click', (event) => {
            if (!event.target.closest('[data-accessibility-widget]')) {
                closeMenu();
            }
            if (!event.target.closest('.navbar')) {
                closeNavMenu();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeMenu();
                closeNavMenu();
            }
        });

        document.addEventListener('mousemove', (event) => {
            const guide = document.querySelector('[data-reading-guide]');
            if (guide) {
                guide.style.top = `${event.clientY}px`;
            }
        }, { passive: true });
    });
})();
