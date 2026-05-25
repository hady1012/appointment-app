(function () {
    const storageKey = 'marketplace-theme';
    const savedTheme = localStorage.getItem(storageKey);
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const initialTheme = savedTheme || (prefersDark ? 'dark' : 'light');

    function applyTheme(theme) {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem(storageKey, theme);

        const toggle = document.querySelector('[data-theme-toggle]');
        if (toggle) {
            const state = theme === 'dark' ? 'On' : 'Off';
            toggle.innerHTML = '<span>Dark mode</span><strong>' + state + '</strong>';
            toggle.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
            toggle.setAttribute('aria-label', toggle.title);
            toggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
        }
    }

    function closeAccessibilityMenu() {
        const widget = document.querySelector('[data-accessibility-widget]');
        const button = document.querySelector('[data-accessibility-toggle]');
        if (widget && button) {
            widget.classList.remove('is-open');
            button.setAttribute('aria-expanded', 'false');
        }
    }

    applyTheme(initialTheme);

    document.addEventListener('DOMContentLoaded', () => {
        if (!document.querySelector('[data-accessibility-widget]')) {
            const widget = document.createElement('div');
            widget.className = 'accessibility-widget';
            widget.dataset.accessibilityWidget = 'true';
            widget.innerHTML = [
                '<button type="button" class="accessibility-toggle" data-accessibility-toggle aria-expanded="false" aria-controls="accessibility-panel">',
                    '<span class="accessibility-toggle-label">Explore your accessibility options</span>',
                    '<span class="accessibility-toggle-divider" aria-hidden="true"></span>',
                    '<span class="accessibility-person" aria-hidden="true"><span></span></span>',
                '</button>',
                '<div class="accessibility-panel" id="accessibility-panel" role="menu">',
                    '<button type="button" class="accessibility-option" data-theme-toggle role="menuitem" aria-pressed="false"></button>',
                '</div>'
            ].join('');
            document.body.appendChild(widget);
        }

        applyTheme(document.documentElement.dataset.theme || initialTheme);

        const accessibilityToggle = document.querySelector('[data-accessibility-toggle]');
        accessibilityToggle?.addEventListener('click', (event) => {
            event.stopPropagation();
            const widget = document.querySelector('[data-accessibility-widget]');
            const isOpen = !widget?.classList.contains('is-open');
            widget?.classList.toggle('is-open', isOpen);
            accessibilityToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        document.querySelector('[data-theme-toggle]')?.addEventListener('click', () => {
            const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
            applyTheme(nextTheme);
        });

        document.addEventListener('click', (event) => {
            if (!event.target.closest('[data-accessibility-widget]')) {
                closeAccessibilityMenu();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeAccessibilityMenu();
            }
        });

        const navbar = document.querySelector('.navbar');
        let lastScrollY = window.scrollY;

        window.addEventListener('scroll', () => {
            if (!navbar) {
                return;
            }

            const currentScrollY = window.scrollY;
            const scrollingDown = currentScrollY > lastScrollY && currentScrollY > 90;
            navbar.classList.toggle('nav-hidden', scrollingDown);
            lastScrollY = currentScrollY;
        }, { passive: true });
    });
})();
