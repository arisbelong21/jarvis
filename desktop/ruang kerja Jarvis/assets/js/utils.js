/**
 * JARVIS Premium Modern Interface - Utility Library
 * Provides essential helper functions for DOM manipulation, 
 * state management, and performance optimization.
 */

const JarvisUtils = (() => {
    'use strict';

    return {
        /**
         * Limits the rate at which a function can fire.
         * Essential for window resizing and input search events.
         * @param {Function} func - The function to debounce.
         * @param {number} wait - Delay in milliseconds.
         * @returns {Function}
         */
        debounce: (func, wait = 200) => {
            let timeout;
            return (...args) => {
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(this, args), wait);
            };
        },

        /**
         * Deeply merges two objects. Useful for updating complex state settings.
         * @param {Object} target - The destination object.
         * @param {Object} source - The source object to merge from.
         * @returns {Object}
         */
        deepMerge: (target, source) => {
            for (const key in source) {
                if (source[key] instanceof Object && key in target) {
                    Object.assign(source[key], JarvisUtils.deepMerge(target[key], source[key]));
                }
            }
            Object.assign(target || {}, source);
            return target;
        },

        /**
         * Helper to create and inject DOM elements efficiently.
         * @param {string} tag - HTML tag name.
         * @param {Object} attributes - Object containing attributes (id, class, etc).
         * @param {string|Node} content - Inner content of the element.
         * @returns {HTMLElement}
         */
        createElement: (tag, attributes = {}, content = '') => {
            const element = document.createElement(tag);
            Object.entries(attributes).forEach(([key, value]) => {
                if (key === 'className') {
                    element.className = value;
                } else if (key === 'classList') {
                    if (Array.isArray(value)) {
                        element.classList.add(...value);
                    } else {
                        element.classList.add(value);
                    }
                } else {
                    element.setAttribute(key, value);
                }
            });
            if (typeof content === 'string') {
                element.innerHTML = content;
            } else if (content instanceof Node) {
                element.appendChild(content);
            }
            return element;
        },

        /**
         * Safely queries an element and handles the error if not found.
         * @param {string} selector - CSS selector.
         * @returns {HTMLElement|null}
         */
        $: (selector) => {
            try {
                return document.querySelector(selector);
            } catch (e) {
                console.error(`JarvisUtils: Error selecting element ${selector}`, e);
                return null;
            }
        },

        /**
         * Smoothly scrolls to a specific element.
         * @param {string|HTMLElement} target - Selector or element.
         */
        scrollToElement: (target) => {
            const element = typeof target === 'string' ? document.querySelector(target) : target;
            if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        },

        /**
         * Formats a timestamp into a readable human format (HH:MM:SS).
         * @returns {string}
         */
        formatTime: () => {
            const now = new Date();
            return now.toLocaleTimeString('en-US', { 
                hour12: false, 
                hour: '2-digit', 
                minute: '2-digit', 
                second: '2-digit' 
            });
        },

        /**
         * Generates a random ID for dynamic elements.
         * @returns {string}
         */
        generateId: (prefix = 'jarvis') => {
            return `${prefix}_${Math.random().toString(36).substr(2, 9)}`;
        },

        /**
         * Trigger a custom event on an element.
         * @param {HTMLElement} element - Target element.
         * @param {string} eventName - Name of event.
         * @param {Object} detail - Data to pass with event.
         */
        emit: (element, eventName, detail = {}) => {
            const event = new CustomEvent(eventName, { detail });
            element.dispatchEvent(event);
        }
    };
})();

// Export for use in app.js (assuming browser environment)
window.JarvisUtils = JarvisUtils;