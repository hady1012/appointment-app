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
        launcherPosition: 'left'
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
            const enabled = button.dataset.a11yPosition === settings.launcherPosition;
            button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            button.classList.toggle('is-active', enabled);
        });

        const widget = document.querySelector('[data-accessibility-widget]');
        if (widget) {
            widget.dataset.position = settings.launcherPosition || 'left';
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
            '<span aria-hidden="true">A</span>',
            '<b>Accessibility</b>',
            '</button>',
            '<section class="accessibility-panel" id="accessibility-panel" aria-label="Accessibility options">',
            '<div class="accessibility-panel-title">',
            '<button type="button" class="accessibility-close" data-accessibility-close aria-label="Close accessibility menu">x</button>',
            '<h2>Accessibility</h2>',
            '<span></span>',
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
            '<div class="accessibility-position" aria-label="Accessibility button position">',
            '<strong>Button position</strong>',
            '<div class="accessibility-position-buttons">',
            '<button type="button" data-a11y-position="left" aria-pressed="true">Left</button>',
            '<button type="button" data-a11y-position="right" aria-pressed="false">Right</button>',
            '</div>',
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

    applyTheme(initialTheme);
    applySettings(readSettings());

    document.addEventListener('DOMContentLoaded', () => {
        buildWidget();
        installFastPageActions();
        applyTheme(root.dataset.theme || initialTheme);
        applySettings(readSettings());

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
                next.launcherPosition = button.dataset.a11yPosition || 'left';
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
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeMenu();
            }
        });

        document.addEventListener('mousemove', (event) => {
            const guide = document.querySelector('[data-reading-guide]');
            if (guide) {
                guide.style.top = `${event.clientY}px`;
            }
        }, { passive: true });

        const navbar = document.querySelector('.navbar');
        if (navbar && window.location.pathname === '/') {
            let lastScrollY = window.scrollY;
            window.addEventListener('scroll', () => {
                const currentScrollY = window.scrollY;
                navbar.classList.toggle('nav-hidden', currentScrollY > lastScrollY && currentScrollY > 90);
                lastScrollY = currentScrollY;
            }, { passive: true });
        }
    });
})();
