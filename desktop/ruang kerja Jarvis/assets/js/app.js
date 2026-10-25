import { debounce, deepMerge, $ , $$ } from './utils.js';

(() => {
    'use strict';

    // -------------------- State Management --------------------
    const defaultState = {
        conversation: [], // { role: 'user'|'jarvis', content: string, timestamp: number }
        settings: {
            theme: 'glass',
            fontSize: 16,
            autoScroll: true
        }
    };

    let state = deepMerge({}, defaultState);

    const STATE_KEY = 'jarvis_modern_state';

    const loadState = () => {
        try {
            const raw = localStorage.getItem(STATE_KEY);
            if (raw) {
                const saved = JSON.parse(raw);
                state = deepMerge(state, saved);
            }
        } catch (e) {
            console.warn('Failed to load Jarvis state:', e);
        }
    };

    const saveState = () => {
        try {
            localStorage.setItem(STATE_KEY, JSON.stringify(state));
        } catch (e) {
            console.warn('Failed to save Jarvis state:', e);
        }
    };

    // -------------------- DOM References --------------------
    const refs = {
        chatContainer: $('#chat-container'),
        inputField: $('#message-input'),
        sendButton: $('#send-button'),
        settingsToggle: $('#settings-toggle'),
        settingsPanel: $('#settings-panel'),
        themeSelect: $('#theme-select'),
        fontSizeInput: $('#font-size-input')
    };

    // -------------------- UI Rendering --------------------
    const createMessageElement = (msg) => {
        const wrapper = document.createElement('div');
        wrapper.className = `message ${msg.role}`;
        wrapper.dataset.timestamp = msg.timestamp;

        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.textContent = msg.content;

        const meta = document.createElement('div');
        meta.className = 'meta';
        const date = new Date(msg.timestamp);
        meta.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        wrapper.appendChild(bubble);
        wrapper.appendChild(meta);
        return wrapper;
    };

    const renderConversation = () => {
        const { chatContainer } = refs;
        chatContainer.innerHTML = '';
        state.conversation.forEach(msg => {
            const el = createMessageElement(msg);
            chatContainer.appendChild(el);
        });
        if (state.settings.autoScroll) {
            scrollToBottom();
        }
    };

    const scrollToBottom = () => {
        const { chatContainer } = refs;
        chatContainer.scrollTo({
            top: chatContainer.scrollHeight,
            behavior: 'smooth'
        });
    };

    const addMessage = (role, content) => {
        const message = {
            role,
            content,
            timestamp: Date.now()
        };
        state.conversation.push(message);
        const el = createMessageElement(message);
        refs.chatContainer.appendChild(el);
        if (state.settings.autoScroll) {
            scrollToBottom();
        }
        saveState();
    };

    // -------------------- Interaction Handlers --------------------
    const handleUserSubmit = async () => {
        const input = refs.inputField.value.trim();
        if (!input) return;
        addMessage('user', input);
        refs.inputField.value = '';
        refs.inputField.disabled = true;
        refs.sendButton.disabled = true;
        addMessage('jarvis', '…'); // placeholder for loading

        try {
            const response = await invokeLegacyJarvis(input);
            // Replace the loading placeholder
            const loadingMsg = state.conversation.find(m => m.role === 'jarvis' && m.content === '…');
            if (loadingMsg) {
                loadingMsg.content = response;
                loadingMsg.timestamp = Date.now();
            } else {
                addMessage('jarvis', response);
            }
            renderConversation();
        } catch (e) {
            console.error('Jarvis processing error:', e);
            const errorMsg = '⚠️ Something went wrong. Please try again.';
            const loadingMsgIndex = state.conversation.findIndex(m => m.role === 'jarvis' && m.content === '…');
            if (loadingMsgIndex !== -1) {
                state.conversation[loadingMsgIndex].content = errorMsg;
                state.conversation[loadingMsgIndex].timestamp = Date.now();
            } else {
                addMessage('jarvis', errorMsg);
            }
            renderConversation();
        } finally {
            refs.inputField.disabled = false;
            refs.sendButton.disabled = false;
            refs.inputField.focus();
        }
    };

    const invokeLegacyJarvis = (input) => {
        return new Promise((resolve, reject) => {
            try {
                // Legacy implementation may expose a global function `jarvisProcess`
                // that can be synchronous or return a Promise.
                const result = typeof window.jarvisProcess === 'function'
                    ? window.jarvisProcess(input)
                    : null;

                if (result && typeof result.then === 'function') {
                    // It's a promise
                    result.then(resolve).catch(reject);
                } else if (typeof result === 'string') {
                    resolve(result);
                } else {
                    // Fallback: simple echo (replace with real integration)
                    resolve(`You said: "${input}"`);
                }
            } catch (err) {
                reject(err);
            }
        });
    };

    const handleKeyPress = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleUserSubmit();
        }
    };

    // -------------------- Settings Management --------------------
    const applyTheme = (theme) => {
        document.documentElement.dataset.theme = theme;
    };

    const applyFontSize = (size) => {
        document.documentElement.style.setProperty('--base-font-size', `${size}px`);
    };

    const toggleSettingsPanel = () => {
        refs.settingsPanel.classList.toggle('open');
    };

    const bindSettings = () => {
        refs.themeSelect.value = state.settings.theme;
        refs.fontSizeInput.value = state.settings.fontSize;

        refs.themeSelect.addEventListener('change', (e) => {
            state.settings.theme = e.target.value;
            applyTheme(state.settings.theme);
            saveState();
        });

        refs.fontSizeInput.addEventListener('input', debounce((e) => {
            const size = parseInt(e.target.value, 10);
            if (!isNaN(size) && size >= 12 && size <= 24) {
                state.settings.fontSize = size;
                applyFontSize(size);
                saveState();
            }
        }, 300));
    };

    // -------------------- Initialization --------------------
    const init = () => {
        loadState();

        // Apply persisted UI preferences
        applyTheme(state.settings.theme);
        applyFontSize(state.settings.fontSize);

        // Render existing conversation
        renderConversation();

        // Bind UI events
        refs.sendButton.addEventListener('click', handleUserSubmit);
        refs.inputField.addEventListener('keydown', handleKeyPress);
        refs.settingsToggle.addEventListener('click', toggleSettingsPanel);
        bindSettings();

        // Auto-resize textarea height
        const autoResize = debounce(() => {
            refs.inputField.style.height = 'auto';
            refs.inputField.style.height = `${refs.inputField.scrollHeight}px`;
        }, 100);
        refs.inputField.addEventListener('input', autoResize);
        autoResize(); // initial sizing

        // Clipboard copy on click of message bubble
        refs.chatContainer.addEventListener('click', (e) => {
            const bubble = e.target.closest('.bubble');
            if (!bubble) return;
            const text = bubble.textContent;
            navigator.clipboard.writeText(text).then(() => {
                bubble.classList.add('copied');
                setTimeout(() => bubble.classList.remove('copied'), 800);
            }).catch(err => {
                console.warn('Clipboard copy failed:', err);
            });
        });
    };

    document.addEventListener('DOMContentLoaded', init);
})();