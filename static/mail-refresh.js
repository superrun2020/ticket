(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MailRefresh = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  function createMailRefreshController(options) {
    const {
      getWorkspace, fetchCursor, refreshList, onMessages, onStatus, isVisible,
      setIntervalFn = setInterval, clearIntervalFn = clearInterval,
    } = options;
    let cursor = null;
    let generation = 0;
    let activePromise = null;
    let timer = null;
    let paused = false;
    let cursorWorkspace = null;

    async function baseline(expectedGeneration) {
      const workspace = getWorkspace();
      try {
        const result = await fetchCursor(null);
        if (expectedGeneration !== generation || workspace !== getWorkspace()) return false;
        cursor = result.cursor;
        cursorWorkspace = workspace;
        return true;
      } catch (error) {
        if (expectedGeneration === generation) onStatus({phase: 'error', error, reason: 'baseline'});
        return false;
      }
    }

    async function refresh(reason = 'poll') {
      if (paused || !isVisible() || activePromise) return false;
      const expectedGeneration = generation;
      const workspace = getWorkspace();
      onStatus({phase: 'loading', reason});
      activePromise = (async () => {
        try {
          const result = await fetchCursor(cursor);
          if (expectedGeneration !== generation || workspace !== getWorkspace()) return false;
          await refreshList(workspace);
          if (expectedGeneration !== generation || workspace !== getWorkspace()) return false;
          cursor = result.cursor;
          onStatus({phase: 'success', reason, refreshedAt: new Date()});
          if (result.messages.length) {
            try { onMessages(result.messages); } catch (error) { /* Alerts are ancillary to refresh. */ }
          }
          return true;
        } catch (error) {
          if (expectedGeneration === generation) onStatus({phase: 'error', error, reason});
          return false;
        } finally {
          activePromise = null;
        }
      })();
      return activePromise;
    }

    async function workspaceChanged() {
      generation++;
      paused = false;
      cursor = null;
      cursorWorkspace = null;
      const expectedGeneration = generation;
      if (activePromise) await activePromise;
      return baseline(expectedGeneration);
    }

    function invalidateWorkspace() {
      generation++;
      cursor = null;
      cursorWorkspace = null;
    }

    function pauseWorkspaceSwitch() {
      generation++;
      paused = true;
    }

    function resumeWorkspace(workspace) {
      if (cursorWorkspace !== workspace) return workspaceChanged();
      paused = false;
      return Promise.resolve(true);
    }

    async function start() {
      await baseline(generation);
      timer = setIntervalFn(() => refresh('poll'), 15000);
    }

    function stop() {
      generation++;
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    }

    return {start, stop, refresh, workspaceChanged, invalidateWorkspace, pauseWorkspaceSwitch, resumeWorkspace, visibilityChanged: () => isVisible() ? refresh('visibility') : Promise.resolve(false)};
  }

  function createAlertPreferences({storage, audio, notifications}) {
    const soundEnabled = () => storage.getItem('ticket-mail-sound') === '1';
    async function setSoundEnabled(enabled) {
      storage.setItem('ticket-mail-sound', enabled ? '1' : '0');
      if (enabled && audio.state === 'suspended') await audio.resume();
    }
    async function requestNotificationPermission() {
      if (notifications.permission === 'default') return notifications.requestPermission();
      return notifications.permission;
    }
    function alert(message) {
      if (soundEnabled()) {
        try { audio.playTone(); } catch (error) { /* A failed tone must not suppress other alerts. */ }
      }
      if (notifications.permission === 'granted') {
        try { notifications.show(message); } catch (error) { /* Notification constructors can throw. */ }
      }
    }
    return {soundEnabled, setSoundEnabled, requestNotificationPermission, alert};
  }

  return {createMailRefreshController, createAlertPreferences};
});
