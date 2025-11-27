

(() => {
  const RAW_DATA = JSON.parse(document.getElementById('digest-data').textContent);
  const SOURCE_STORAGE_KEY = 'arxivDigestSource';
  const PREF_STORAGE_KEY = 'arxivDigestPreferences';
  const DISPLAY_MODE_STORAGE_KEY = 'arxivDigestDisplayMode';
  const READ_LIST_STORAGE_KEY = 'arxivDigestReadList';
  const DISPLAY_MODE_CLASSES = {
    title: 'display-mode-title',
    authors: 'display-mode-authors',
    full: 'display-mode-full',
  };
  const DISPLAY_MODE_OPTIONS = [
    { key: 'title', label: 'Title only' },
    { key: 'authors', label: 'Title & authors' },
    { key: 'full', label: 'Full details' },
  ];
  let modalHandlersBound = false;

  const SOURCE_KEYS = Object.keys(RAW_DATA.sources || {});
  if (!SOURCE_KEYS.length) {
    return;
  }
  const generatedAt = RAW_DATA.generated_at || '';
  const initialPreferences = normalizePreferences(RAW_DATA.preferences || {});

  const state = {
    source: loadStoredSource(),
    preferences: loadStoredPreferences(),
    isEditingPreferences: false,
    activeSection: 'stats',
    displayMode: loadStoredDisplayMode() || 'authors',
    expandedArticles: new Set(),
    lastModalTrigger: null,
    readList: loadStoredReadList(),
    readListEditingId: '',
  };

  if (!RAW_DATA.sources[state.source]) {
    state.source = RAW_DATA.default_source && RAW_DATA.sources[RAW_DATA.default_source]
      ? RAW_DATA.default_source
      : SOURCE_KEYS[0];
  }

  const elements = {
    sourceSwitcher: document.getElementById('source-switcher'),
    displayModeControls: document.getElementById('display-mode-controls'),
    nav: document.querySelector('.sidebar nav'),
    preferencesView: document.getElementById('preferences-view'),
    preferencesForm: document.getElementById('preferences-form'),
    favoriteAuthorsView: document.getElementById('favorite-authors-view'),
    keywordsView: document.getElementById('keywords-view'),
    favoritesInput: document.getElementById('favorite-authors-input'),
    keywordsInput: document.getElementById('keywords-input'),
    editPreferences: document.getElementById('edit-preferences'),
    cancelPreferences: document.getElementById('cancel-preferences'),
    resetPreferences: document.getElementById('reset-preferences'),
    preferencesStatusView: document.getElementById('preferences-status-view'),
    preferencesStatus: document.getElementById('preferences-status'),
    readListBody: document.getElementById('read-list-body'),
    readListCount: document.getElementById('read-list-count'),
    readListClear: document.getElementById('read-list-clear'),
    dateSwitcherForm: document.getElementById('date-switcher-form'),
    dateSwitcherInput: document.getElementById('date-switcher-input'),
    overviewSummary: document.getElementById('overview-summary'),
    overviewBody: document.getElementById('overview-body'),
    statsBody: document.getElementById('stats-body'),
    favoritesBody: document.getElementById('favorite-body'),
    keywordsBody: document.getElementById('keywords-body'),
    categoriesBody: document.getElementById('categories-body'),
    headerSource: document.getElementById('meta-source'),
    headerDate: document.getElementById('meta-date'),
    headerGenerated: document.getElementById('meta-generated'),
    headerTotal: document.getElementById('meta-total'),
    footerSource: document.getElementById('footer-source'),
    abstractModal: document.getElementById('abstract-modal'),
    abstractModalClose: document.getElementById('abstract-modal-close'),
    abstractModalTitle: document.getElementById('abstract-modal-title'),
    abstractModalBody: document.getElementById('abstract-modal-body'),
    abstractModalId: document.getElementById('abstract-modal-id'),
    abstractModalAuthors: document.getElementById('abstract-modal-authors'),
    abstractModalSubjects: document.getElementById('abstract-modal-subjects'),
    abstractModalSummary: document.getElementById('abstract-modal-summary'),
    abstractModalAbstract: document.getElementById('abstract-modal-abstract'),
    abstractModalOriginal: document.getElementById('abstract-modal-original'),
    abstractModalPdf: document.getElementById('abstract-modal-pdf'),
  };

  document.addEventListener('click', handleQuickViewClick);
  document.addEventListener('click', handlePaperClick);
  document.addEventListener('click', handleReadListClick);
  document.addEventListener('keydown', handlePaperKeydown);
  document.addEventListener('keydown', handleGlobalKeydown);

  if (elements.editPreferences) {
    elements.editPreferences.addEventListener('click', () => {
      state.isEditingPreferences = true;
      setStatus('');
      renderPreferencesPanel();
      if (elements.favoritesInput) {
        elements.favoritesInput.focus();
      }
    });
  }

  if (elements.cancelPreferences) {
    elements.cancelPreferences.addEventListener('click', () => {
      state.isEditingPreferences = false;
      setStatus('');
      renderPreferencesPanel();
    });
  }

  if (elements.dateSwitcherForm && elements.dateSwitcherInput) {
    const sourceDate = (RAW_DATA.sources[state.source] && RAW_DATA.sources[state.source].date) || '';
    if (sourceDate) {
      elements.dateSwitcherInput.value = sourceDate;
    }
    elements.dateSwitcherForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const value = resolveDateInputValue(elements.dateSwitcherInput);
      if (!value) {
        window.alert('Please choose a date as YYYY-MM-DD.');
        return;
      }
      const target = `index-${value}.html`;
      window.location.href = target;
    });
  }

  if (elements.readListClear) {
    elements.readListClear.addEventListener('click', () => {
      state.readList = [];
      state.readListEditingId = '';
      saveReadList(state.readList);
      renderReadList();
      updateReadListButtons();
    });
  }

  if (elements.preferencesForm) {
    elements.preferencesForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const nextPrefs = normalizePreferences({
        favorite_authors: elements.favoritesInput ? elements.favoritesInput.value : '',
        keywords: elements.keywordsInput ? elements.keywordsInput.value : '',
      });
      state.preferences = nextPrefs;
      state.isEditingPreferences = false;
      savePreferences(nextPrefs);
      renderAll({ resetActiveSection: false });
      setStatus('Preferences saved.');
    });
  }

  if (elements.resetPreferences) {
    elements.resetPreferences.addEventListener('click', () => {
      state.preferences = normalizePreferences(initialPreferences);
      state.isEditingPreferences = true;
      savePreferences(state.preferences);
      renderAll({ resetActiveSection: false });
      setStatus('Preferences reset to defaults.');
    });
  }

  renderAll({ resetActiveSection: true });

  function renderAll(options = {}) {
    if (options.resetActiveSection) {
      state.activeSection = 'stats';
    }
    renderSourceButtons();
    renderDisplayModeControls();
    renderPreferencesPanel();
    renderReadList();

    const sourceData = RAW_DATA.sources[state.source];
    if (!sourceData) {
      return;
    }

    const articles = sourceData.articles || [];
    pruneExpandedArticles(articles);
    applyDisplayModeClass();
    updateHeader(sourceData);
    updateDateSwitcher(sourceData);
    const overviewCount = renderOverview(sourceData, articles);
    renderStats(sourceData);
    const favoriteCount = renderFavorites(sourceData, articles);
    const keywordCount = renderKeywords(sourceData, articles);
    renderNavigation(sourceData, overviewCount, favoriteCount, keywordCount);
    updateFooter(sourceData);
    attachSectionHandlers();
    attachModalHandlers();
    setActiveSection(state.activeSection);
    updatePaperAria();
    updateReadListButtons();
  }

  function renderSourceButtons() {
    const container = elements.sourceSwitcher;
    if (!container) return;
    container.innerHTML = SOURCE_KEYS.map((key) => {
      const label = RAW_DATA.sources[key].label || key;
      const active = key === state.source ? 'is-active' : '';
      return `<button type="button" class="source-button ${active}" data-source="${key}">${escapeHtml(label)}</button>`;
    }).join('');
    Array.from(container.querySelectorAll('button[data-source]')).forEach((button) => {
      button.addEventListener('click', () => {
        const nextSource = button.getAttribute('data-source');
        if (!nextSource || nextSource === state.source || !RAW_DATA.sources[nextSource]) return;
        state.source = nextSource;
        saveSource(nextSource);
        setStatus('');
        state.activeSection = 'stats';
        state.expandedArticles.clear();
        renderAll({ resetActiveSection: true });
      });
    });
  }

  function renderDisplayModeControls() {
    const container = elements.displayModeControls;
    if (!container) return;
    const label = `<span class="display-mode__label">Paper view</span>`;
    const buttons = DISPLAY_MODE_OPTIONS.map(({ key, label: buttonLabel }) => {
      return `<button type="button" class="display-mode__button" data-mode="${key}" aria-pressed="false">${escapeHtml(buttonLabel)}</button>`;
    }).join('');
    container.innerHTML = `${label}${buttons}`;
    Array.from(container.querySelectorAll('button[data-mode]')).forEach((button) => {
      button.addEventListener('click', () => {
        const mode = button.getAttribute('data-mode');
        if (!mode) return;
        setDisplayMode(mode);
      });
    });
    updateDisplayModeButtons();
  }

  function setDisplayMode(mode) {
    const normalized = normalizeDisplayMode(mode);
    if (normalized === state.displayMode) {
      return;
    }
    state.displayMode = normalized;
    saveDisplayMode(normalized);
    applyDisplayModeClass();
    updateDisplayModeButtons();
    updatePaperAria();
  }

  function applyDisplayModeClass() {
    const targetClass = DISPLAY_MODE_CLASSES[state.displayMode] || DISPLAY_MODE_CLASSES.full;
    const classList = document.body.classList;
    Object.values(DISPLAY_MODE_CLASSES).forEach((cls) => classList.remove(cls));
    classList.add(targetClass);
  }

  function updateDisplayModeButtons() {
    const container = elements.displayModeControls;
    if (!container) return;
    const activeMode = state.displayMode;
    Array.from(container.querySelectorAll('button[data-mode]')).forEach((button) => {
      const mode = button.getAttribute('data-mode');
      const isActive = mode === activeMode;
      button.classList.toggle('is-active', isActive);
      button.setAttribute('aria-pressed', String(isActive));
    });
  }

  function renderPreferencesPanel() {
    const favorites = state.preferences.favorite_authors || [];
    const keywords = state.preferences.keywords || [];

    if (elements.favoriteAuthorsView) {
      elements.favoriteAuthorsView.innerHTML = favorites.length
        ? favorites.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('')
        : '<span class="preferences-empty">None</span>';
    }
    if (elements.keywordsView) {
      elements.keywordsView.innerHTML = keywords.length
        ? keywords.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('')
        : '<span class="preferences-empty">None</span>';
    }

    if (state.isEditingPreferences) {
      if (elements.preferencesView) elements.preferencesView.hidden = true;
      if (elements.preferencesForm) elements.preferencesForm.hidden = false;
      updatePreferenceInputs();
    } else {
      if (elements.preferencesView) elements.preferencesView.hidden = false;
      if (elements.preferencesForm) elements.preferencesForm.hidden = true;
    }
  }

  function renderReadList() {
    const container = elements.readListBody;
    const items = state.readList || [];
    if (elements.readListCount) {
      elements.readListCount.textContent = String(items.length);
    }
    if (elements.readListClear) {
      elements.readListClear.disabled = items.length === 0;
    }
    if (!container) return;
    if (!items.length) {
      container.innerHTML = '<p class="empty-state">No papers saved yet.</p>';
      return;
    }
    const sorted = [...items].sort((a, b) => (b.addedAt || 0) - (a.addedAt || 0));
    container.innerHTML = sorted.map((item, index) => renderReadListItem(item, index)).join('');
  }

  function renderOverview(sourceData, articles) {
    const body = elements.overviewBody;
    if (!body) return 0;
    const summary = elements.overviewSummary;
    const total = articles.length;
    const sourceLabel = sourceData.label || state.source;
    const plural = total === 1 ? '' : 's';
    if (summary) {
      summary.textContent = total + ' paper' + plural + ' from ' + sourceLabel + '.';
    }
    body.innerHTML = articles.map(renderArticleCard).join('') || '<p class="empty-state">No papers available.</p>';
    return total;
  }

  function renderStats(sourceData) {
    const body = elements.statsBody;
    if (!body) return;
    const stats = sourceData.stats || {};
    const total = stats.total || 0;
    const uniqueAuthors = stats.unique_authors || 0;
    const totalAuthorships = stats.total_authorships || 0;
    const averageAuthors = (stats.average_authors || 0).toFixed(2);
    const topAuthors = (stats.top_authors || []).map(([name, count]) => `<li>${escapeHtml(name)} (${count})</li>`).join('') || '<li>None</li>';
    const topKeywords = (stats.top_phrases || []).map(([phrase, count]) => `<li>${escapeHtml(phrase)} (${count})</li>`).join('') || '<li>None</li>';
    const sectionCounts = Object.entries(stats.section_counts || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([section, count]) => `<li>${escapeHtml(section)} (${count})</li>`)
      .join('') || '<li>None</li>';

    body.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <h3>Papers</h3>
          <p>Total papers: ${total}</p>
          <p>Avg authors per paper: ${averageAuthors}</p>
        </div>
        <div class="stat-card">
          <h3>Authors</h3>
          <p>Unique authors: ${uniqueAuthors}</p>
          <p>Total author mentions: ${totalAuthorships}</p>
        </div>
        <div class="stat-card">
          <h3>Top Authors</h3>
          <ul>${topAuthors}</ul>
        </div>
        <div class="stat-card">
          <h3>Popular Keywords</h3>
          <ul>${topKeywords}</ul>
        </div>
        <div class="stat-card">
          <h3>Section Breakdown</h3>
          <ul>${sectionCounts}</ul>
        </div>
      </div>
    `;
  }

  function renderFavorites(sourceData, articles) {
    const body = elements.favoritesBody;
    if (!body) return 0;
    const favorites = state.preferences.favorite_authors || [];
    const matches = filterByFavoriteAuthors(articles, favorites);
    body.innerHTML = buildWatcherSectionContent(favorites, matches, 'Add authors in the sidebar to highlight researchers you care about.');
    return matches.length;
  }

  function renderKeywords(sourceData, articles) {
    const body = elements.keywordsBody;
    if (!body) return 0;
    const keywords = state.preferences.keywords || [];
    const matches = filterByKeywords(articles, keywords);
    body.innerHTML = buildWatcherSectionContent(keywords, matches, 'Track important topics by adding keywords in the sidebar.');
    return matches.length;
  }

  function buildWatcherSectionContent(items, matches, emptyMessage) {
    const chips = (items || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('');
    const summary = items.length
      ? `<div class="watcher-summary">Watching <strong>${items.length}</strong> entr${items.length === 1 ? 'y' : 'ies'}.<div class="chip-set">${chips}</div></div>`
      : `<div class="watcher-summary">${emptyMessage}</div>`;
    const articlesHtml = matches.length
      ? matches.map(renderArticleCard).join('')
      : '<p class="empty-state">No papers matched the current filters.</p>';
    return `${summary}${articlesHtml}`;
  }

  function renderCategories(sourceData, articles) {
    const body = elements.categoriesBody;
    if (!body) return [];
    const groups = buildSectionGrouping(articles);
    if (!groups.length) {
      body.innerHTML = '<p class="empty-state">No categories available for this source.</p>';
      return [];
    }
    const sectionsHtml = groups.map(({ sectionId, sectionLabel, count, subjects }) => {
      const subjectHtml = subjects.map(({ subjectId, subjectLabel, items }) => `
        <div class="subject-group" id="${subjectId}">
          <div class="subject-group__header">
            <h4>${escapeHtml(subjectLabel)}</h4>
            <span class="count-chip">${formatCount(items.length)}</span>
          </div>
          ${items.map(renderArticleCard).join('')}
        </div>
      `).join('');
      return `
        <div class="category-block" id="${sectionId}">
          <div class="category-block__header">
            <h3>${escapeHtml(sectionLabel)}</h3>
            <span class="count-chip">${formatCount(count)}</span>
          </div>
          <div class="subject-grid">
            ${subjectHtml}
          </div>
        </div>
      `;
    }).join('');
    body.innerHTML = sectionsHtml;
    return groups.map(({ sectionId, sectionLabel, count, subjects }) => ({
      id: sectionId,
      label: `${sectionLabel} (${count})`,
      children: subjects.map(({ subjectId, subjectLabel, items }) => ({
        id: subjectId,
        label: `${subjectLabel} (${items.length})`,
      })),
    }));
  }

  function renderNavigation(sourceData, overviewCount, favoriteCount, keywordCount) {
    if (!elements.nav) return;
    const navItems = [
      { id: 'workspace', label: 'Workspace' },
      { id: 'stats', label: 'Statistics' },
      { id: 'overview', label: `All Papers (${overviewCount})` },
      { id: 'favorite', label: `Favorite Authors (${favoriteCount})` },
      { id: 'keyword', label: `Watched Keywords (${keywordCount})` },
    ];
    elements.nav.innerHTML = buildNavList(navItems);
  }

  function buildNavList(items, level = 1) {
    if (!items || !items.length) return '';
    const listClass = `nav-list nav-level-${level}`;
    const inner = items.map((item) => {
      const children = buildNavList(item.children || [], level + 1);
      return `<li class="nav-item nav-level-${level}"><a href="#${item.id}">${escapeHtml(item.label)}</a>${children}</li>`;
    }).join('');
    return `<ul class="${listClass}">${inner}</ul>`;
  }

  function updateHeader(sourceData) {
    if (elements.headerSource) elements.headerSource.textContent = `Source: ${sourceData.label || state.source}`;
    if (elements.headerDate) elements.headerDate.textContent = `Date: ${sourceData.date || ''}`;
    if (elements.headerGenerated) elements.headerGenerated.textContent = `Generated at: ${generatedAt}`;
    if (elements.headerTotal) elements.headerTotal.textContent = `Total papers: ${(sourceData.stats && sourceData.stats.total) || 0}`;
  }

  function updateDateSwitcher(sourceData) {
    if (!elements.dateSwitcherInput) return;
    if (sourceData && sourceData.date) {
      elements.dateSwitcherInput.value = sourceData.date;
    }
  }

  function resolveDateInputValue(input) {
    if (!input) return '';
    // Try valueAsDate first (most reliable for date inputs)
    if (input.valueAsDate instanceof Date && !Number.isNaN(input.valueAsDate.getTime())) {
      const year = input.valueAsDate.getFullYear();
      const month = String(input.valueAsDate.getMonth() + 1).padStart(2, '0');
      const day = String(input.valueAsDate.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    }
    // Fallback to parsing the value string
    const raw = input.value ? input.value.trim() : '';
    if (raw) {
      // Try to match YYYY-MM-DD format
      const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (match) {
        return raw;
      }
      // Try to parse as Date and format
      const parsed = new Date(raw);
      if (parsed instanceof Date && !Number.isNaN(parsed.getTime())) {
        const year = parsed.getFullYear();
        const month = String(parsed.getMonth() + 1).padStart(2, '0');
        const day = String(parsed.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
      }
    }
    return '';
  }

  function updateFooter(sourceData) {
    if (!elements.footerSource) return;
    elements.footerSource.textContent = sourceData.label || state.source;
    if (sourceData.url) {
      elements.footerSource.setAttribute('href', sourceData.url);
    }
  }

  function attachSectionHandlers() {
    const toggles = Array.from(document.querySelectorAll('.section-toggle'));
    toggles.forEach((toggle) => {
      toggle.onclick = () => {
        const targetId = toggle.getAttribute('data-target');
        if (!targetId) return;
        const section = document.getElementById(targetId);
        if (!section) return;
        const willExpand = section.classList.contains('is-collapsed');
        setSectionState(section, willExpand);
        if (willExpand) {
          state.activeSection = section.id;
          setActiveSection(section.id);
        }
      };
    });
    const navLinks = elements.nav ? Array.from(elements.nav.querySelectorAll('a[href^="#"]')) : [];
    navLinks.forEach((link) => {
      link.onclick = (event) => {
        const href = link.getAttribute('href');
        if (!href || !href.startsWith('#')) return;
        const targetId = href.slice(1);
        const targetElement = document.getElementById(targetId);
        if (!targetElement) return;
        const container = targetElement.classList.contains('content-section')
          ? targetElement
          : targetElement.closest('.content-section');
        if (!container) return;
        event.preventDefault();
        state.activeSection = container.id;
        setActiveSection(container.id, targetElement);
      };
    });
  }

  function setActiveSection(sectionId, focusTarget) {
    state.activeSection = sectionId || 'stats';
    const sections = Array.from(document.querySelectorAll('.content-section'));
    sections.forEach((section) => {
      if (section.dataset.staticSection === 'true') {
        section.classList.remove('is-hidden', 'is-collapsed');
        return;
      }
      const isActive = section.id === state.activeSection;
      section.classList.toggle('is-hidden', !isActive);
      if (section.dataset.collapsible === 'true') {
        setSectionState(section, isActive);
      } else {
        section.classList.toggle('is-collapsed', !isActive);
      }
    });
    const activeSection = document.getElementById(state.activeSection);
    if (activeSection) {
      const scrollTarget = focusTarget && activeSection.contains(focusTarget) ? focusTarget : activeSection;
      expandAncestors(scrollTarget);
      scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (elements.nav) {
      const navLinks = Array.from(elements.nav.querySelectorAll('a[href^="#"]'));
      navLinks.forEach((link) => {
        const href = link.getAttribute('href');
        const id = href ? href.slice(1) : '';
        link.classList.toggle('is-active', id === state.activeSection);
      });
    }
  }

  function updatePaperAria() {
    const cards = Array.from(document.querySelectorAll('.paper'));
    cards.forEach((card) => {
      const paperId = card.getAttribute('data-paper-id') || '';
      const isUserExpanded = paperId && state.expandedArticles.has(paperId);
      const isExpanded = state.displayMode === 'full' || Boolean(isUserExpanded);
      card.setAttribute('aria-expanded', String(isExpanded));
      card.classList.toggle('paper--expanded', Boolean(isUserExpanded));
    });
  }

  function handleQuickViewClick(event) {
    const button = event.target.closest('.js-view-abstract');
    if (!button) return;
    const articleId = button.getAttribute('data-article-id') || '';
    const details = buildModalDetails(articleId, button);
    if (!details.url && !details.title) return;
    event.preventDefault();
    event.stopPropagation();
    openAbstractModal(details, button);
  }

  function handlePaperClick(event) {
    const paper = event.target.closest('.paper');
    if (!paper) return;
    if (event.target.closest('a')) return;
    if (event.target.closest('.js-view-abstract')) return;
    if (event.target.closest('.js-readlist-toggle')) return;
    togglePaperExpansion(paper);
  }

  function handlePaperKeydown(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const paper = event.target.closest('.paper');
    if (!paper) return;
    if (event.key === ' ') {
      event.preventDefault();
    }
    if (event.target.closest('.js-readlist-toggle')) return;
    togglePaperExpansion(paper);
  }

  function togglePaperExpansion(paper) {
    const paperId = paper.getAttribute('data-paper-id') || '';
    if (!paperId) return;
    if (state.expandedArticles.has(paperId)) {
      state.expandedArticles.delete(paperId);
    } else {
      state.expandedArticles.add(paperId);
    }
    updatePaperAria();
  }

  function handleReadListClick(event) {
    const toggleButton = event.target.closest('.js-readlist-toggle');
    if (toggleButton) {
      const articleId = toggleButton.getAttribute('data-article-id') || '';
      if (!articleId) return;
      event.preventDefault();
      event.stopPropagation();
      if (isInReadList(articleId)) {
        removeFromReadList(articleId);
      } else {
        const details = buildModalDetails(articleId, toggleButton);
        const sourceLabel = (RAW_DATA.sources[state.source] && RAW_DATA.sources[state.source].label) || state.source;
        addToReadList({
          id: articleId,
          title: decodeHtml(details.title) || articleId,
          absUrl: decodeHtml(details.url),
          pdfUrl: decodeHtml(details.pdfUrl),
          authors: decodeHtml(details.authors),
          source: sourceLabel,
        });
      }
      return;
    }

    const removeButton = event.target.closest('[data-readlist-remove]');
    if (removeButton) {
      const targetId = removeButton.getAttribute('data-readlist-remove') || '';
      if (targetId) {
        removeFromReadList(targetId);
      }
      return;
    }

    const editButton = event.target.closest('[data-readlist-edit]');
    if (editButton) {
      const targetId = editButton.getAttribute('data-readlist-edit') || '';
      state.readListEditingId = targetId;
      renderReadList();
      return;
    }

    const cancelButton = event.target.closest('[data-readlist-cancel]');
    if (cancelButton) {
      state.readListEditingId = '';
      renderReadList();
      return;
    }

    const saveButton = event.target.closest('[data-readlist-save]');
    if (saveButton) {
      const targetId = saveButton.getAttribute('data-readlist-save') || '';
      if (!targetId) return;
      const selector = `[data-readlist-note-input="${targetId}"]`;
      const noteInput = elements.readListBody ? elements.readListBody.querySelector(selector) : null;
      const note = noteInput ? noteInput.value : '';
      updateReadListNote(targetId, note);
    }
  }

  function attachModalHandlers() {
    if (modalHandlersBound) return;
    modalHandlersBound = true;
    if (elements.abstractModalClose) {
      elements.abstractModalClose.addEventListener('click', () => closeAbstractModal());
    }
    if (elements.abstractModal) {
      elements.abstractModal.addEventListener('click', (event) => {
        if (event.target && event.target.getAttribute('data-modal-dismiss') === 'true') {
          closeAbstractModal();
        }
      });
    }
  }

  function handleGlobalKeydown(event) {
    if (event.key === 'Escape' && isModalOpen()) {
      event.preventDefault();
      closeAbstractModal();
    }
  }

  function isModalOpen() {
    return Boolean(elements.abstractModal && elements.abstractModal.classList.contains('is-open'));
  }

  function openAbstractModal(details, trigger) {
    if (!elements.abstractModal) return;
    state.lastModalTrigger = trigger || null;
    const resolvedTitle = decodeHtml(details.title) || 'Preview abstract';
    const resolvedUrl = decodeHtml(details.url);
    const resolvedAuthors = decodeHtml(details.authors);
    const resolvedSubjects = decodeHtml(details.subjects);
    const resolvedSummary = decodeHtml(details.summary);
    const resolvedAbstract = decodeHtml(details.abstract);
    const resolvedId = decodeHtml(details.arxivId);
    const resolvedPdf = decodeHtml(details.pdfUrl);
    elements.abstractModal.classList.add('is-open');
    elements.abstractModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    if (elements.abstractModalClose) {
      elements.abstractModalClose.focus();
    }
    if (elements.abstractModalTitle) {
      elements.abstractModalTitle.textContent = resolvedTitle;
    }
    if (elements.abstractModalBody) {
      elements.abstractModalBody.scrollTop = 0;
    }
    if (elements.abstractModalId) {
      elements.abstractModalId.textContent = resolvedId ? `ID: ${resolvedId}` : '';
      elements.abstractModalId.hidden = !resolvedId;
    }
    if (elements.abstractModalAuthors) {
      elements.abstractModalAuthors.textContent = resolvedAuthors ? `Authors: ${resolvedAuthors}` : '';
      elements.abstractModalAuthors.hidden = !resolvedAuthors;
    }
    if (elements.abstractModalSubjects) {
      elements.abstractModalSubjects.textContent = resolvedSubjects ? `Subjects: ${resolvedSubjects}` : '';
      elements.abstractModalSubjects.hidden = !resolvedSubjects;
    }
    if (elements.abstractModalSummary) {
      elements.abstractModalSummary.textContent = resolvedSummary ? `Summary: ${resolvedSummary}` : '';
      elements.abstractModalSummary.hidden = !resolvedSummary;
    }
    if (elements.abstractModalAbstract) {
      elements.abstractModalAbstract.textContent = resolvedAbstract || 'No abstract available.';
    }
    if (elements.abstractModalOriginal) {
      if (resolvedUrl) {
        elements.abstractModalOriginal.href = resolvedUrl;
        elements.abstractModalOriginal.hidden = false;
      } else {
        elements.abstractModalOriginal.href = '#';
        elements.abstractModalOriginal.hidden = true;
      }
    }
    if (elements.abstractModalPdf) {
      if (resolvedPdf) {
        elements.abstractModalPdf.href = resolvedPdf;
        elements.abstractModalPdf.hidden = false;
      } else {
        elements.abstractModalPdf.href = '#';
        elements.abstractModalPdf.hidden = true;
      }
    }
  }

  function closeAbstractModal() {
    if (!elements.abstractModal) return;
    elements.abstractModal.classList.remove('is-open');
    elements.abstractModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    if (state.lastModalTrigger && typeof state.lastModalTrigger.focus === 'function') {
      state.lastModalTrigger.focus();
    }
    state.lastModalTrigger = null;
  }

  function setSectionState(section, expanded) {
    if (expanded) {
      section.classList.add('is-expanded');
      section.classList.remove('is-collapsed');
    } else {
      section.classList.add('is-collapsed');
      section.classList.remove('is-expanded');
    }
    const toggle = section.querySelector('.section-toggle');
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.textContent = expanded ? 'Hide section' : 'Show section';
    }
  }

  function expandAncestors(element) {
    if (!element) return;
    let parent = element.closest('[data-collapsible="true"]');
    while (parent) {
      setSectionState(parent, true);
      parent = parent.parentElement ? parent.parentElement.closest('[data-collapsible="true"]') : null;
    }
  }

  function renderArticleCard(article) {
    const articleId = String(article.arxiv_id || article.id || '');
    const title = escapeHtml(article.title);
    const authors = escapeHtml((article.authors || []).join(', '));
    const subjects = escapeHtml((article.subjects || []).join('; '));
    const abstract = escapeHtml(article.abstract);
    const summary = escapeHtml(article.summary || '');
    const absUrl = escapeHtml(article.abs_url);
    const pdfUrl = article.pdf_url ? escapeHtml(article.pdf_url) : '';
    const pdfLink = pdfUrl ? `<a href="${pdfUrl}" target="_blank" rel="noopener">PDF</a>` : '';
    const quickViewButton = `<button type="button" class="link-button quick-view-button js-view-abstract" data-abs-url="${absUrl}" data-article-title="${title}" data-article-authors="${authors}" data-article-subjects="${subjects}" data-article-abstract="${abstract}" data-article-summary="${summary}" data-article-id="${escapeHtml(article.arxiv_id)}" data-article-pdf="${pdfUrl}">Quick view</button>`;
    const inReadList = isInReadList(articleId);
    const readListLabel = inReadList ? 'In read list' : 'Add to read list';
    const readListActiveClass = inReadList ? ' is-active' : '';
    const readListButton = `<button type="button" class="link-button readlist-toggle js-readlist-toggle${readListActiveClass}" data-abs-url="${absUrl}" data-article-title="${title}" data-article-authors="${authors}" data-article-subjects="${subjects}" data-article-abstract="${abstract}" data-article-summary="${summary}" data-article-id="${escapeHtml(article.arxiv_id)}" data-article-pdf="${pdfUrl}" aria-pressed="${inReadList}">${readListLabel}</button>`;
    const keywords = Array.isArray(article.keywords) ? article.keywords : [];
    const keywordBadges = keywords.length
      ? `<span class="keyword-tags">${keywords.map((keyword) => `<span class="keyword-tag">${escapeHtml(keyword)}</span>`).join('')}</span>`
      : '';
    const linkItems = [`<a href="${absUrl}" target="_blank" rel="noopener">Abstract</a>`, pdfLink].filter(Boolean).join(' ');
    const isUserExpanded = state.expandedArticles.has(articleId);
    const ariaExpanded = state.displayMode === 'full' || isUserExpanded;
    const expandedClass = isUserExpanded ? ' paper--expanded' : '';
    const summaryBlock = summary
      ? `<p class="summary">${summary}</p>`
      : '';
    return `
      <article class="paper${expandedClass}" data-paper-id="${escapeHtml(articleId)}" tabindex="0" aria-expanded="${ariaExpanded}">
        <h3><a href="${absUrl}" target="_blank" rel="noopener">${title}</a>${keywordBadges}${quickViewButton}${readListButton}</h3>
        <p class="meta">
          <span class="id">${escapeHtml(article.arxiv_id)}</span>
          <span class="authors">${authors}</span>
        </p>
        ${summaryBlock}
        <p class="subjects">${subjects}</p>
        <p class="links">${linkItems}</p>
      </article>
    `;
  }

  function renderReadListItem(item, index) {
    const rawId = String(item.id || '');
    const id = escapeHtml(rawId);
    const title = escapeHtml(item.title || rawId);
    const absUrl = escapeHtml(item.absUrl || item.url || '#');
    const deleteButton = `<button type="button" class="readlist-action readlist-action--danger readlist-action--inline readlist-action--dot" data-readlist-remove="${id}" aria-label="Delete from read list">D</button>`;
    const titleLabel = `${index + 1}. `;
    return `
      <div class="readlist-item" data-readlist-id="${id}">
        <div class="readlist-item__title-row">
          <div class="readlist-title">
            ${deleteButton}<span class="readlist-index">${titleLabel}</span><a href="${absUrl}" target="_blank" rel="noopener">${title}</a>
          </div>
        </div>
      </div>
    `;
  }

  function buildSectionGrouping(articles) {
    const sections = new Map();
    articles.forEach((article) => {
      const sectionKey = article.section_type || 'Other';
      const subjectKey = article.primary_subject || 'Other';
      if (!sections.has(sectionKey)) {
        sections.set(sectionKey, new Map());
      }
      const subjectMap = sections.get(sectionKey);
      if (!subjectMap.has(subjectKey)) {
        subjectMap.set(subjectKey, []);
      }
      subjectMap.get(subjectKey).push(article);
    });

    return Array.from(sections.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([sectionName, subjectMap]) => {
        const sectionId = `category-${slugify(sectionName)}`;
        const subjects = Array.from(subjectMap.entries())
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([subjectName, items]) => ({
            subjectId: `${sectionId}-${slugify(subjectName, 'subject')}`,
            subjectLabel: subjectName,
            items,
          }));
        const count = subjects.reduce((sum, entry) => sum + entry.items.length, 0);
        return {
          sectionId,
          sectionLabel: sectionName,
          count,
          subjects,
        };
      });
  }

  function addToReadList(entry) {
    const [normalized] = normalizeReadListItems([entry]);
    if (!normalized) return;
    const next = state.readList.filter((item) => item.id !== normalized.id);
    next.push(normalized);
    state.readListEditingId = '';
    setReadList(next);
  }

  function removeFromReadList(id) {
    const next = state.readList.filter((item) => item.id !== id);
    if (state.readListEditingId === id) {
      state.readListEditingId = '';
    }
    setReadList(next);
  }

  function updateReadListNote(id, note) {
    let changed = false;
    const next = state.readList.map((item) => {
      if (item.id !== id) return item;
      changed = true;
      return { ...item, note: String(note || '') };
    });
    if (!changed) return;
    state.readListEditingId = '';
    setReadList(next);
  }

  function setReadList(nextList) {
    state.readList = normalizeReadListItems(nextList);
    saveReadList(state.readList);
    renderReadList();
    updateReadListButtons();
  }

  function updateReadListButtons() {
    const buttons = Array.from(document.querySelectorAll('.js-readlist-toggle'));
    if (!buttons.length) return;
    const ids = new Set(state.readList.map((item) => item.id));
    buttons.forEach((button) => {
      const id = button.getAttribute('data-article-id') || '';
      const isActive = id && ids.has(id);
      button.classList.toggle('is-active', Boolean(isActive));
      button.setAttribute('aria-pressed', String(Boolean(isActive)));
      button.textContent = isActive ? 'In read list' : 'Add to read list';
    });
  }

  function isInReadList(articleId) {
    return state.readList.some((item) => item.id === articleId);
  }

  function filterByFavoriteAuthors(articles, favoriteAuthors) {
    const favorites = (favoriteAuthors || []).map((name) => name.toLowerCase()).filter(Boolean);
    if (!favorites.length) return [];
    return articles.filter((article) => {
      const authorLower = article.authors.map((name) => name.toLowerCase());
      return favorites.some((fav) => authorLower.some((author) => author.includes(fav)));
    });
  }

  function filterByKeywords(articles, keywords) {
    const needles = (keywords || []).map((kw) => kw.toLowerCase()).filter(Boolean);
    if (!needles.length) return [];
    return articles.filter((article) => {
      const articleKeywords = Array.isArray(article.keywords)
        ? article.keywords.map((kw) => kw.toLowerCase())
        : [];
      if (articleKeywords.length) {
        return needles.some((needle) => articleKeywords.includes(needle));
      }
      const fallback = `${article.title} ${article.abstract}`.toLowerCase();
      return needles.some((needle) => fallback.includes(needle));
    });
  }

  function pruneExpandedArticles(articles) {
    const ids = new Set(
      (articles || []).map((article) => String(article.arxiv_id || article.id || '')).filter((value) => value),
    );
    Array.from(state.expandedArticles).forEach((storedId) => {
      if (!ids.has(storedId)) {
        state.expandedArticles.delete(storedId);
      }
    });
  }

  function updatePreferenceInputs() {
    if (elements.favoritesInput) {
      elements.favoritesInput.value = state.preferences.favorite_authors.join('\n');
    }
    if (elements.keywordsInput) {
      elements.keywordsInput.value = state.preferences.keywords.join('\n');
    }
  }

  function setStatus(message) {
    if (elements.preferencesStatus) {
      elements.preferencesStatus.textContent = state.isEditingPreferences ? message : '';
    }
    if (elements.preferencesStatusView) {
      elements.preferencesStatusView.textContent = state.isEditingPreferences ? '' : message;
    }
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function slugify(text, fallback = 'section') {
    const slug = String(text || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return slug || fallback;
  }

  function formatCount(value) {
    const count = Number(value) || 0;
    return `${count} paper${count === 1 ? '' : 's'}`;
  }

  function normalizeDisplayMode(value) {
    if (typeof value !== 'string') {
      return 'full';
    }
    const normalized = value.trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(DISPLAY_MODE_CLASSES, normalized) ? normalized : 'full';
  }

  function normalizePreferences(raw) {
    const normalizeList = (value) => {
      if (Array.isArray(value)) {
        const cleaned = value.map((item) => String(item).trim()).filter((item) => item);
        return Array.from(new Set(cleaned));
      }
      if (typeof value === 'string') {
        const cleaned = value
          .split(/[\n,]/)
          .map((item) => item.trim())
          .filter((item) => item);
        return Array.from(new Set(cleaned));
      }
      return [];
    };
    return {
      favorite_authors: normalizeList(raw.favorite_authors),
      keywords: normalizeList(raw.keywords),
    };
  }

  function normalizeReadListItems(value) {
    if (!Array.isArray(value)) return [];
    return value.map((item) => {
      const id = String(item && item.id ? item.id : '').trim();
      const title = String(item && item.title ? item.title : '').trim() || id;
      if (!id || !title) return null;
      return {
        id,
        title,
        absUrl: String(item && (item.absUrl || item.url) ? item.absUrl || item.url : '').trim(),
        pdfUrl: String(item && item.pdfUrl ? item.pdfUrl : '').trim(),
        authors: String(item && item.authors ? item.authors : '').trim(),
        source: String(item && item.source ? item.source : '').trim(),
        note: typeof (item && item.note) === 'string' ? item.note : '',
        addedAt: Number(item && item.addedAt) || Date.now(),
      };
    }).filter(Boolean);
  }

  function loadStoredDisplayMode() {
    try {
      const stored = localStorage.getItem(DISPLAY_MODE_STORAGE_KEY);
      return stored ? normalizeDisplayMode(stored) : '';
    } catch (_) {
      return '';
    }
  }

  function saveDisplayMode(mode) {
    try {
      localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, normalizeDisplayMode(mode));
    } catch (_) {}
  }

  function loadStoredReadList() {
    try {
      const raw = localStorage.getItem(READ_LIST_STORAGE_KEY);
      return raw ? normalizeReadListItems(JSON.parse(raw)) : [];
    } catch (_) {
      return [];
    }
  }

  function saveReadList(list) {
    try {
      localStorage.setItem(READ_LIST_STORAGE_KEY, JSON.stringify(normalizeReadListItems(list)));
    } catch (_) {}
  }

  function loadStoredSource() {
    try {
      return localStorage.getItem(SOURCE_STORAGE_KEY) || '';
    } catch (_) {
      return '';
    }
  }

  function saveSource(value) {
    try {
      localStorage.setItem(SOURCE_STORAGE_KEY, value);
    } catch (_) {}
  }

  function loadStoredPreferences() {
    try {
      const raw = localStorage.getItem(PREF_STORAGE_KEY);
      return raw ? normalizePreferences(JSON.parse(raw)) : normalizePreferences(initialPreferences);
    } catch (_) {
      return normalizePreferences(initialPreferences);
    }
  }

  function savePreferences(prefs) {
    try {
      localStorage.setItem(PREF_STORAGE_KEY, JSON.stringify(prefs));
    } catch (_) {}
  }

  function decodeHtml(value) {
    if (!value) return '';
    const textarea = document.createElement('textarea');
    textarea.innerHTML = value;
    return textarea.value;
  }

  function buildModalDetails(articleId, button) {
    const sourceData = RAW_DATA.sources[state.source] || {};
    const articles = Array.isArray(sourceData.articles) ? sourceData.articles : [];
    const article = articleId ? articles.find((item) => {
      const id = String(item.arxiv_id || item.id || '');
      return id === articleId;
    }) : null;
    const dataset = button.dataset || {};
    const details = {
      title: article && article.title ? article.title : dataset.articleTitle || '',
      url: article && article.abs_url ? article.abs_url : dataset.absUrl || '',
      authors: Array.isArray(article && article.authors) ? article.authors.join(', ') : dataset.articleAuthors || '',
      subjects: Array.isArray(article && article.subjects) ? article.subjects.join('; ') : dataset.articleSubjects || '',
      abstract: article && article.abstract ? article.abstract : dataset.articleAbstract || '',
      summary: article && article.summary ? article.summary : dataset.articleSummary || '',
      arxivId: article && article.arxiv_id ? article.arxiv_id : dataset.articleId || '',
      pdfUrl: article && article.pdf_url ? article.pdf_url : dataset.articlePdf || '',
    };
    return details;
  }
})();

  
