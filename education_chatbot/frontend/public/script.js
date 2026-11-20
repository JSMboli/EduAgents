'use strict';

const $ = (id) => document.getElementById(id);
const question = $('question');
const modeSelect = $('mode-select');
const messages = $('messages');
const profileDialog = $('profile-dialog');
const confirmDialog = $('confirm-dialog');
const iconPath = '/assets/vendor/icons.svg#';
const svgNamespace = 'http://www.w3.org/2000/svg';
const modes = {
    auto: { name: 'Auto', description: 'Find the right learning mode', icon: 'sparkles' },
    explain: { name: 'Explain', description: 'Understand a concept', icon: 'book-open' },
    practice: { name: 'Practice', description: 'Try an interactive question', icon: 'list-checks' },
    plan: { name: 'Study plan', description: 'Turn a goal into steps', icon: 'scan-text' },
};

const starterTopics = {
    '5–8': ['Why do we see the moon?', 'Can you quiz me on adding?', 'Help me practise reading.'],
    '9–12': ['How do fractions work?', 'Quiz me on the solar system.', 'Help me practise my times tables.'],
    '13–15': ['Explain photosynthesis.', 'Quiz me on linear equations.', 'Help me plan a week of revision.'],
    '16–17': ['How does a derivative work?', 'Quiz me on cell biology.', 'Help me plan for an exam.'],
    '18+': ['Explain opportunity cost.', 'Quiz me on basic statistics.', 'Build a study plan for a new language.'],
};

const state = {
    workspace: null,
    chat: null,
    busy: false,
    sending: false,
    drafts: new Map(),
    requests: new Map(),
    mode: 'auto',
};

function preference(name, value) {
    try {
        if (value === undefined) return sessionStorage.getItem(name);
        if (value === null) sessionStorage.removeItem(name);
        else sessionStorage.setItem(name, value);
    } catch {
        return null;
    }
}

const remembered = preference('edubot-mode');
if (Object.hasOwn(modes, remembered)) state.mode = remembered;

class RequestError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

async function api(path, method = 'GET', body) {
    let response;
    try {
        response = await fetch('/api/' + path, {
            method,
            credentials: 'same-origin',
            signal: AbortSignal.timeout(62_000),
            ...(body !== undefined && {
                body: JSON.stringify(body),
                headers: { 'Content-Type': 'application/json' },
            }),
        });
    } catch {
        throw new RequestError('Connection interrupted. Please try again.', 0);
    }
    if (response.status === 204) return;
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) {
        throw new RequestError(typeof data?.detail === 'string' ? data.detail : 'Couldn’t complete that request.', response.status);
    }
    return data;
}

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function icon(name, className) {
    const svg = document.createElementNS(svgNamespace, 'svg');
    svg.setAttribute('aria-hidden', 'true');
    if (className) svg.setAttribute('class', className);
    const use = document.createElementNS(svgNamespace, 'use');
    use.setAttribute('href', iconPath + name);
    svg.append(use);
    return svg;
}

function announce(text) {
    $('announcer').textContent = text;
}

function showError(error, reconnect = false) {
    $('error-message').textContent = error.message;
    $('reload').hidden = !reconnect;
    $('error-bar').hidden = false;
    $('error-bar').scrollIntoView({ block: 'nearest' });
}

function clearError() {
    $('error-bar').hidden = true;
    $('error-message').textContent = '';
}

function renderHistory() {
    const filter = $('search').value.trim().toLowerCase();
    const chats = (state.workspace?.chats ?? []).filter((chat) => chat.title.toLowerCase().includes(filter));
    const date = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
    const entries = chats.map((chat) => {
        const button = element('button');
        button.type = 'button';
        button.disabled = state.busy;
        if (state.chat?.id === chat.id) button.setAttribute('aria-current', 'true');
        const label = element('span');
        label.append(element('strong', '', chat.title), element('small', '', date.format(new Date(chat.updated_at))));
        button.append(icon('message-circle'), label);
        button.addEventListener('click', () => openChat(chat.id));
        const item = element('li');
        item.append(button);
        return item;
    });
    $('chat-list').replaceChildren(...entries);
    $('history-empty').hidden = entries.length > 0;
    $('history-empty').textContent = filter ? 'No matching conversations.' : 'Your conversations will appear here.';
}

function renderStarters() {
    const age = state.workspace?.age_band;
    $('starter-grid').replaceChildren(...(starterTopics[age] ?? []).map((topic, index) => {
        const button = element('button', 'starter');
        button.type = 'button';
        button.disabled = !state.workspace.tutor_configured;
        button.append(icon(['book-open', 'list-checks', 'scan-text'][index]), element('strong', '', topic));
        button.addEventListener('click', () => {
            question.value = topic;
            setMode(['explain', 'practice', 'plan'][index]);
            updateComposer();
            question.focus();
        });
        return button;
    }));
}

function initModes() {
    for (const [value, mode] of Object.entries(modes)) {
        const button = element('button', 'mode-option');
        button.type = 'button';
        button.dataset.mode = value;
        const graphic = element('span', 'mode-glyph');
        graphic.append(icon(mode.icon));
        const label = element('span');
        label.append(element('strong', '', mode.name), element('small', '', mode.description));
        button.append(graphic, label, icon('check', 'mode-check'));
        button.addEventListener('click', () => setMode(value));
        $('modes').append(button);
        const option = element('option', '', mode.name);
        option.value = value;
        modeSelect.append(option);
    }
    setMode(state.mode);
}

function setMode(mode) {
    state.mode = mode;
    preference('edubot-mode', mode);
    modeSelect.value = mode;
    for (const button of $('modes').children) button.setAttribute('aria-pressed', button.dataset.mode === mode);
}

function renderWorkspace() {
    const age = state.workspace?.age_band;
    const tutor = state.workspace?.tutor_configured;
    $('connection').dataset.state = tutor ? 'ready' : 'offline';
    $('connection-label').textContent = tutor ? 'Tutor configured' : state.workspace ? 'Tutor setup needed' : 'Offline';
    $('welcome').hidden = Boolean(state.chat);
    $('onboarding').hidden = !state.workspace || Boolean(age);
    $('starter-heading').textContent = state.chat ? 'Try a question' : 'Start with a question';
    $('starters').hidden = !age || Boolean(state.chat?.turns.length);
    $('conversation').hidden = !state.chat;
    $('composer-wrap').hidden = !age;
    $('new-chat').disabled = !age || state.busy;
    $('profile-button').disabled = !age || state.busy;
    $('privacy-button').disabled = !age || state.busy;
    $('sidebar-age').textContent = age ? 'Age group ' + age : 'Set up your workspace';
    $('privacy-text').textContent = age
        ? 'Your age group is ' + age + '. Conversations and your profile are saved for this browser.'
        : 'Set up a learning profile to tailor your sessions to your age.';
    $('progress').hidden = !state.sending;
    $('delete-chat').disabled = state.busy;
    $('export-chat').disabled = !state.chat?.turns.length;
    if (state.chat) $('conversation-heading').textContent = state.chat.title;
    updateComposer();
    renderHistory();
}

function draftKey() {
    return state.chat?.id ?? '';
}

function updateComposer() {
    const length = question.value.length;
    state.drafts.set(draftKey(), question.value);
    $('counter').textContent = length + ' / 2500';
    question.disabled = !state.workspace?.tutor_configured;
    question.readOnly = state.sending;
    question.placeholder = question.disabled
        ? 'Add a Groq API key on the server to start chatting.'
        : 'Ask EduBot anything about your studies…';
    modeSelect.disabled = state.sending;
    $('send').disabled = state.busy || !state.workspace?.tutor_configured || !question.value.trim();
}

function renderMarkdown(content, text) {
    if (!window.marked || !window.DOMPurify) {
        content.textContent = text;
        return;
    }
    content.append(window.DOMPurify.sanitize(window.marked.parse(text), {
        RETURN_DOM_FRAGMENT: true,
        USE_PROFILES: { html: true },
        FORBID_TAGS: ['img', 'form', 'input', 'button', 'video', 'audio', 'style'],
        FORBID_ATTR: ['style', 'id', 'name'],
    }));
    for (const link of content.querySelectorAll('a')) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
    }
    if (window.renderMathInElement) {
        window.renderMathInElement(content, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
            ],
            throwOnError: false,
            trust: false,
        });
    }
}

function renderQuiz(turn) {
    const quiz = element('fieldset', 'quiz');
    quiz.append(element('legend', '', 'Practice question'), element('p', '', turn.quiz.question));
    const options = element('div', 'quiz-options');
    const graded = turn.quiz.result;
    for (const [index, option] of turn.quiz.options.entries()) {
        const button = element('button', 'quiz-option', option);
        button.type = 'button';
        if (graded) {
            button.disabled = true;
            if (index === graded.correct) {
                button.classList.add('correct');
                button.append(document.createTextNode(' ✓'));
            } else if (index === graded.selected) {
                button.classList.add('incorrect');
                button.append(document.createTextNode(' · Your answer'));
            }
        } else {
            button.addEventListener('click', () => grade(turn.id, index, options));
        }
        options.append(button);
    }
    quiz.append(options);
    if (graded) {
        const lead = graded.correct === graded.selected ? 'Correct. ' : 'Answer: ' + turn.quiz.options[graded.correct] + '. ';
        quiz.append(element('p', 'quiz-feedback', lead + graded.explanation));
    }
    return quiz;
}

function renderTurn(turn) {
    const article = element('article', 'turn');
    const user = element('div', 'user-message', turn.message);
    const card = element('div', 'assistant-message');
    const label = element('div', 'message-label');
    label.append(icon('sparkles'), element('span', '', 'EduBot · ' + (turn.blocked ? 'Safety response' : modes[turn.mode].name)));
    const content = element('div', 'message-body');
    renderMarkdown(content, turn.reply);
    card.append(label, content);
    if (turn.quiz) card.append(renderQuiz(turn));
    const footer = element('div', 'message-footer');
    const copy = element('button', 'text-button', 'Copy reply');
    copy.type = 'button';
    copy.prepend(icon('clipboard'));
    copy.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(turn.reply);
            announce('Reply copied.');
        } catch {
            announce('Copy is unavailable. Select the reply text to copy it.');
        }
    });
    const steps = element('details');
    const list = element('ol');
    list.append(...turn.steps.map((step) => element('li', '', step)));
    steps.append(element('summary', '', 'Agent activity'), list);
    footer.append(copy, steps);
    card.append(footer);
    article.append(user, card);
    return article;
}

function showChat(chat) {
    state.chat = chat;
    preference('edubot-chat', chat?.id ?? null);
    question.value = state.drafts.get(draftKey()) ?? '';
    messages.replaceChildren(...(chat?.turns ?? []).map(renderTurn));
    renderWorkspace();
    renderStarters();
}

function updateChatList(chat) {
    state.workspace.chats = [chat, ...state.workspace.chats.filter((item) => item.id !== chat.id)];
}

async function initialise() {
    clearError();
    try {
        state.workspace = await api('session');
        const active = preference('edubot-chat');
        if (state.workspace.chats.some((chat) => chat.id === active)) {
            showChat(await api('chats/' + active));
        } else {
            showChat(null);
        }
    } catch (error) {
        showError(error, true);
        renderWorkspace();
    }
}

function setDrawer(open) {
    $('sidebar').classList.toggle('open', open);
    $('nav-overlay').hidden = !open;
    $('open-nav').setAttribute('aria-expanded', String(open));
    $('workspace').inert = open;
    if (open) $('close-nav').focus();
}

async function updateAge(input, submit) {
    if (state.busy) return;
    clearError();
    state.busy = true;
    submit.disabled = true;
    $('reset-workspace').disabled = true;
    renderWorkspace();
    try {
        state.workspace = await api('session', 'PUT', { age: Number(input.value) });
        input.value = '';
        if (profileDialog.open) profileDialog.close();
        renderWorkspace();
        renderStarters();
        announce('Age group saved.');
        if (!question.disabled) question.focus();
    } catch (error) {
        if (profileDialog.open) profileDialog.close();
        showError(error);
    } finally {
        state.busy = false;
        submit.disabled = false;
        $('reset-workspace').disabled = false;
        renderWorkspace();
    }
}

async function openChat(id) {
    if (state.busy) return;
    clearError();
    setDrawer(false);
    state.busy = true;
    renderWorkspace();
    try {
        const chat = await api('chats/' + id);
        showChat(chat);
        $('conversation-heading').setAttribute('tabindex', '-1');
        $('conversation-heading').focus();
    } catch (error) {
        if (error.status === 404 || error.status === 428) await initialise();
        showError(error);
    } finally {
        state.busy = false;
        renderWorkspace();
    }
}

async function grade(turnId, option, options) {
    const chat = state.chat;
    for (const button of options.children) button.disabled = true;
    try {
        const turn = await api('chats/' + chat.id + '/quiz/' + turnId, 'POST', { option });
        const index = chat.turns.findIndex(({ id }) => id === turnId);
        chat.turns[index] = turn;
        if (chat === state.chat) {
            const next = renderQuiz(turn);
            options.closest('fieldset').replaceWith(next);
            next.querySelector('.quiz-feedback').tabIndex = -1;
            next.querySelector('.quiz-feedback').focus();
        }
        announce(turn.quiz.result.selected === turn.quiz.result.correct ? 'Correct answer.' : 'Answer checked.');
    } catch (error) {
        for (const button of options.children) button.disabled = false;
        showError(error);
    }
}

async function send(event) {
    event.preventDefault();
    const message = question.value.trim();
    if (!message || state.busy || !state.workspace?.tutor_configured) return;
    const mode = state.mode;
    clearError();
    state.busy = true;
    state.sending = true;
    renderWorkspace();
    try {
        if (!state.chat) {
            const chat = await api('chats', 'POST', {});
            updateChatList(chat);
            state.drafts.set(chat.id, question.value);
            state.drafts.delete('');
            showChat({ ...chat, turns: [] });
        }
        const chat = state.chat;
        let request = state.requests.get(chat.id);
        if (request?.message !== message || request.mode !== mode) {
            request = { chat_id: chat.id, request_id: crypto.randomUUID(), message, mode };
            state.requests.set(chat.id, request);
        }
        const turn = await api('chat', 'POST', request);
        const known = chat.turns.some(({ id }) => turn.id === id);
        if (!known) {
            chat.turns.push(turn);
            const article = renderTurn(turn);
            messages.append(article);
            article.scrollIntoView({ block: 'start' });
        }
        state.requests.delete(chat.id);
        question.value = '';
        if (chat.title === 'New conversation' && !turn.blocked) {
            const title = turn.message.replace(/\s+/g, ' ');
            const letters = Array.from(title);
            chat.title = letters.length > 58 ? letters.slice(0, 55).join('').trimEnd() + '…' : title;
        }
        chat.updated_at = new Date().toISOString();
        const { id, title, updated_at } = chat;
        updateChatList({ id, title, updated_at });
        announce('EduBot replied.');
    } catch (error) {
        if (error.status === 428) {
            state.drafts.clear();
            state.requests.clear();
            question.value = '';
            await initialise();
        }
        showError(error);
    } finally {
        state.busy = false;
        state.sending = false;
        renderWorkspace();
        if (!question.disabled && question.getBoundingClientRect().top < window.innerHeight) {
            question.focus({ preventScroll: true });
        }
    }
}

function openProfile() {
    if (state.busy || !state.workspace?.age_band) return;
    setDrawer(false);
    $('profile-description').textContent = 'Current age group ' + state.workspace.age_band + '.';
    profileDialog.showModal();
}

function confirmDelete(title, description) {
    $('confirm-title').textContent = title;
    $('confirm-description').textContent = description;
    confirmDialog.returnValue = '';
    confirmDialog.showModal();
    return new Promise((resolve) => {
        confirmDialog.addEventListener('close', () => resolve(confirmDialog.returnValue === 'confirm'), { once: true });
    });
}

async function deleteWorkspace() {
    profileDialog.close();
    if (!await confirmDelete('Clear this workspace?', 'This deletes the saved age group, all conversations, and quiz results.')) return;
    try {
        await api('session', 'DELETE');
        state.drafts.clear();
        state.requests.clear();
        $('search').value = '';
        clearError();
        await initialise();
        $('age').focus();
        announce('Workspace cleared.');
    } catch (error) {
        showError(error);
    }
}

async function deleteChat() {
    if (!state.chat || state.busy) return;
    const id = state.chat.id;
    if (!await confirmDelete('Delete this conversation?', 'This permanently removes the conversation and its quiz results.')) return;
    try {
        await api('chats/' + id, 'DELETE');
        state.workspace.chats = state.workspace.chats.filter((chat) => chat.id !== id);
        state.drafts.delete(id);
        state.requests.delete(id);
        showChat(null);
        clearError();
        announce('Conversation deleted.');
    } catch (error) {
        showError(error);
    }
}

function exportChat() {
    const chat = state.chat;
    if (!chat?.turns.length) return;
    const parts = ['# EduBot conversation', '', chat.title, ''];
    for (const turn of chat.turns) {
        parts.push('## You', '', turn.message, '', '## EduBot', '', turn.reply, '');
        if (turn.quiz) {
            parts.push('### Practice question', '', turn.quiz.question, '', ...turn.quiz.options.map((option) => '- ' + option), '');
            const result = turn.quiz.result;
            if (result) parts.push('Your answer: ' + turn.quiz.options[result.selected], '', result.explanation, '');
        }
    }
    const url = URL.createObjectURL(new Blob([parts.join('\n')], { type: 'text/markdown;charset=utf-8' }));
    const link = element('a');
    link.href = url;
    link.download = 'edubot-conversation.md';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    announce('Conversation exported.');
}

for (const [form, input] of [['age-form', 'age'], ['edit-age-form', 'edit-age']]) {
    $(form).addEventListener('submit', (event) => {
        event.preventDefault();
        updateAge($(input), event.submitter);
    });
}
$('chat-form').addEventListener('submit', send);
question.addEventListener('input', updateComposer);
question.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (!$('send').disabled) $('chat-form').requestSubmit();
    }
});
modeSelect.addEventListener('change', () => setMode(modeSelect.value));
$('search').addEventListener('input', renderHistory);
$('new-chat').addEventListener('click', () => {
    clearError();
    setDrawer(false);
    showChat(null);
    if (!question.disabled) question.focus();
});
$('open-nav').addEventListener('click', () => setDrawer(true));
for (const id of ['close-nav', 'nav-overlay']) {
    $(id).addEventListener('click', () => { setDrawer(false); $('open-nav').focus(); });
}
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('sidebar').classList.contains('open')) {
        setDrawer(false);
        $('open-nav').focus();
    }
});
matchMedia('(max-width: 700px)').addEventListener('change', () => setDrawer(false));
$('profile-button').addEventListener('click', openProfile);
$('privacy-button').addEventListener('click', openProfile);
profileDialog.querySelector('.close-dialog').addEventListener('click', () => profileDialog.close());
$('reset-workspace').addEventListener('click', deleteWorkspace);
$('delete-chat').addEventListener('click', deleteChat);
$('export-chat').addEventListener('click', exportChat);
$('dismiss-error').addEventListener('click', clearError);
$('reload').addEventListener('click', initialise);
initModes();
initialise();
