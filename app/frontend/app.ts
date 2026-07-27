// Self-hosted htmx (no CDN -- required for a CSP without a third-party
// script-src exception) plus the small set of UI behaviors that used to be
// inline onclick/onmouseover handlers in app/templates/projects/*.html.
import 'htmx.org';

// @ts-ignore
import './app-ui.css';

function closeSidebar(): void {
    document.getElementById('app-shell')?.classList.remove('sidebar-open');
}

function openSidebar(): void {
    document.getElementById('app-shell')?.classList.add('sidebar-open');
}

function showModal(modalId: string): void {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'flex';
    }
}

function hideModal(modalId: string): void {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

function toggleAllRequirementCheckboxes(master: HTMLInputElement): void {
    document
        .querySelectorAll<HTMLInputElement>('input[name="ids"]')
        .forEach((cb) => {
            cb.checked = master.checked;
        });
}

function openSplitModal(reqId: string): void {
    const form = document.getElementById('split-form') as HTMLFormElement | null;
    if (form) {
        form.action = `/requirements/${reqId}/split`;
    }
    showModal('split-modal');
}

document.addEventListener('DOMContentLoaded', () => {
    // Sidebar toggle (mobile nav).
    document.querySelectorAll('.sidebar-overlay').forEach((el) => {
        el.addEventListener('click', closeSidebar);
    });
    document.querySelectorAll('.menu-toggle-btn').forEach((el) => {
        el.addEventListener('click', openSidebar);
    });

    // Generic modal open/close via data attributes.
    document.querySelectorAll<HTMLElement>('[data-modal-open]').forEach((el) => {
        el.addEventListener('click', () => {
            const targetId = el.getAttribute('data-modal-open');
            if (targetId) {
                showModal(targetId);
            }
        });
    });
    document.querySelectorAll<HTMLElement>('[data-modal-backdrop]').forEach((el) => {
        el.addEventListener('click', () => {
            const targetId = el.getAttribute('data-modal-backdrop') || el.id;
            hideModal(targetId);
        });
    });
    document.querySelectorAll<HTMLElement>('[data-modal-panel]').forEach((el) => {
        el.addEventListener('click', (event) => event.stopPropagation());
    });
    document.querySelectorAll<HTMLElement>('[data-modal-close]').forEach((el) => {
        el.addEventListener('click', () => {
            const targetId = el.getAttribute('data-modal-close');
            if (targetId) {
                hideModal(targetId);
            }
        });
    });

    // Compliance matrix: select-all checkbox.
    const toggleAllCheckbox = document.getElementById(
        'toggle-all-checkbox'
    ) as HTMLInputElement | null;
    toggleAllCheckbox?.addEventListener('click', () => {
        toggleAllRequirementCheckboxes(toggleAllCheckbox);
    });

    // Compliance matrix: split-requirement modal trigger (rendered per-row,
    // including rows swapped in later by htmx -- delegate on the document).
    document.addEventListener('click', (event) => {
        const trigger = (event.target as HTMLElement)?.closest<HTMLElement>(
            '[data-split-open]'
        );
        if (trigger) {
            const reqId = trigger.getAttribute('data-split-open');
            if (reqId) {
                openSplitModal(reqId);
            }
        }
    });
});
