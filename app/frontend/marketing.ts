import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import './marketing.css';

gsap.registerPlugin(ScrollTrigger);

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

document.addEventListener('DOMContentLoaded', () => {
    // Add js-enabled class to HTML root for progressive enhancement
    document.documentElement.classList.add('js-enabled');

    // 1. Transparent to solid navbar transition
    const header = document.querySelector('.m-nav');
    window.addEventListener('scroll', () => {
        if (window.scrollY > 30) {
            header?.classList.add('scrolled');
        } else {
            header?.classList.remove('scrolled');
        }
    });

    // 2. SVG Pipeline Motion
    if (!reduceMotion) {
        const pulsePaths = document.querySelectorAll('.m-pulse-path');
        pulsePaths.forEach((path) => {
            gsap.to(path, {
                strokeDashoffset: -32,
                repeat: -1,
                ease: 'none',
                duration: 2.0,
            });
        });

        const nodeDots = document.querySelectorAll('.glow-dot');
        gsap.fromTo(nodeDots,
            { scale: 0.9, opacity: 0.8 },
            {
                scale: 1.15,
                opacity: 1,
                transformOrigin: 'center',
                repeat: -1,
                yoyo: true,
                duration: 1.8,
                stagger: 0.3,
                ease: 'sine.inOut'
            }
        );
    }

    // 3. Accessible Screenshot Annotations
    const annotBtns = document.querySelectorAll('.annot-btn');
    annotBtns.forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const wasActive = btn.classList.contains('active');
            
            // Close all
            annotBtns.forEach((b) => {
                b.classList.remove('active');
                b.setAttribute('aria-expanded', 'false');
            });

            // Toggle active state
            if (!wasActive) {
                btn.classList.add('active');
                btn.setAttribute('aria-expanded', 'true');
            }
        });

        // Close on escape key
        btn.addEventListener('keydown', (e: Event) => {
            const keyboardEvent = e as KeyboardEvent;
            if (keyboardEvent.key === 'Escape') {
                btn.classList.remove('active');
                btn.setAttribute('aria-expanded', 'false');
                (btn as HTMLElement).blur();
            }
        });
    });

    // Close annotation tooltips when clicking outside
    document.addEventListener('click', () => {
        annotBtns.forEach((btn) => {
            btn.classList.remove('active');
            btn.setAttribute('aria-expanded', 'false');
        });
    });

    // 4. Desktop Workflow Pinning
    const mediaQuery = window.matchMedia('(min-width: 769px)');

    function setupWorkflowPinning() {
        if (reduceMotion || !mediaQuery.matches) {
            ScrollTrigger.getAll().forEach(t => t.kill());
            
            // Deactivate JS overrides to let CSS display visual scenes naturally
            document.documentElement.classList.remove('js-enabled');
            return;
        }

        document.documentElement.classList.add('js-enabled');
        const steps = gsap.utils.toArray('.m-workflow-step-node') as HTMLElement[];
        const scenes = gsap.utils.toArray('.m-workflow-visual-scene') as HTMLElement[];

        // Clear existing ScrollTriggers
        ScrollTrigger.getAll().forEach(t => t.kill());

        steps.forEach((step, idx) => {
            const sceneId = step.getAttribute('data-scene');
            
            ScrollTrigger.create({
                trigger: step,
                start: 'top 45%',
                end: 'bottom 45%',
                onEnter: () => activateScene(idx),
                onEnterBack: () => activateScene(idx),
            });
        });

        function activateScene(index: number) {
            steps.forEach((s) => s.classList.remove('active'));
            scenes.forEach((sc) => sc.classList.remove('active'));

            steps[index]?.classList.add('active');
            scenes[index]?.classList.add('active');

            const activeScene = scenes[index];
            if (activeScene) {
                // Subtle content slides inside active scene
                const contents = activeScene.querySelectorAll('.scene-content-anim');
                if (contents.length > 0) {
                    gsap.fromTo(contents,
                        { opacity: 0, y: 8 },
                        { opacity: 1, y: 0, duration: 0.4, stagger: 0.1, ease: 'power1.out' }
                    );
                }
            }
        }

        // Init first scene
        activateScene(0);
    }

    setupWorkflowPinning();

    window.addEventListener('resize', () => {
        ScrollTrigger.refresh();
    });

    mediaQuery.addEventListener('change', () => {
        setupWorkflowPinning();
    });
});
