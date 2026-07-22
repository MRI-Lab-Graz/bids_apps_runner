/**
 * Generalized "section header + following siblings" accordion builder.
 *
 * Extracted from what was a single-purpose initializeRunnerMainAccordion()
 * in the inline <script> in templates/index.html, scoped only to the direct
 * children of #runnerForm. That scoping was the whole bug: five other
 * top-level section headers on the Run App page (Container & Tools,
 * Execution, Runner Overrides, Resource Controls, Preview & Submit) live
 * outside #runnerForm and were never wrapped by any working fold mechanism
 * at all -- permanently expanded, permanently uncollapsible. Generalizing
 * this into a reusable function, callable once per container, fixes all
 * five with the same proven logic instead of five bespoke patches.
 *
 * Same plain classic <script src="...">, global-scope pattern as
 * static/js/project_loader.js and static/js/cohort_panel.js.
 */

function buildSectionAccordion(container, options = {}) {
    if (!container) return;
    const exclusive = !!options.exclusive;

    const headers = Array.from(container.children).filter((node) =>
        node.nodeType === 1 && node.classList.contains('section-header')
    );
    if (!headers.length) return;

    headers.forEach((header) => {
        const section = document.createElement('section');
        section.className = 'main-accordion-item';

        const body = document.createElement('div');
        body.className = 'main-accordion-body';

        header.classList.add('main-accordion-header');
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');
        header.setAttribute('aria-expanded', 'false');

        container.insertBefore(section, header);
        section.appendChild(header);
        section.appendChild(body);

        let cursor = section.nextSibling;
        while (cursor) {
            const next = cursor.nextSibling;
            if (
                cursor.nodeType === 1 &&
                cursor.classList &&
                cursor.classList.contains('section-header')
            ) {
                break;
            }
            body.appendChild(cursor);
            cursor = next;
        }
    });

    const items = Array.from(container.querySelectorAll(':scope > .main-accordion-item'));

    function closeItem(item) {
        item.classList.remove('is-open');
        const header = item.querySelector('.main-accordion-header');
        if (header) header.setAttribute('aria-expanded', 'false');
    }

    function openItem(item) {
        if (exclusive) {
            items.forEach((other) => {
                if (other !== item) closeItem(other);
            });
        }
        item.classList.add('is-open');
        const header = item.querySelector('.main-accordion-header');
        if (header) header.setAttribute('aria-expanded', 'true');
    }

    items.forEach((item) => {
        const header = item.querySelector('.main-accordion-header');
        if (!header) return;

        header.addEventListener('click', (event) => {
            if (event.target.closest('a, button, input, select, textarea, label')) {
                return;
            }
            if (item.classList.contains('is-open')) {
                closeItem(item);
            } else {
                openItem(item);
            }
        });

        header.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            if (item.classList.contains('is-open')) {
                closeItem(item);
            } else {
                openItem(item);
            }
        });

        // Keep all sections collapsed on initial load.
        closeItem(item);
    });
}
