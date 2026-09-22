/* BlockChores front end.
 *
 * Deliberately plain ES5: no arrow functions, no let/const, no template
 * strings, no fetch, no Promise.  It has to run on Safari 9 (the last
 * version a 1st generation iPad mini can install).
 */
(function () {
  'use strict';

  var DAY_MS = 86400000;
  var DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var CACHE_KEY = 'blockchores.state';
  var REFRESH_MS = 60000;

  var state = null;          // last snapshot from the server
  var view = 'today';        // today | tasks | history
  var areaFilter = 'all';
  var pending = {};          // task id -> true while a write is in flight
  var editing = null;        // task id being edited, or null for a new task
  var weekdays = [1];        // weekday picker selection in the editor
  var inFlight = 0;
  var lastTap = 0;

  // ---------------------------------------------------------------- utils

  function byId(id) { return document.getElementById(id); }

  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* 'YYYY-MM-DD' -> local Date.  Never hand this string to new Date(),
     old WebKit reads it as UTC and the day slips. */
  function toDate(text) {
    if (!text) { return null; }
    var bits = String(text).split('-');
    if (bits.length !== 3) { return null; }
    var d = new Date(Number(bits[0]), Number(bits[1]) - 1, Number(bits[2]));
    return isNaN(d.getTime()) ? null : d;
  }

  function toISO(d) {
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function today() {
    return (state && state.today) ? state.today : toISO(new Date());
  }

  function dayDiff(fromISO, toISOstr) {
    var a = toDate(fromISO);
    var b = toDate(toISOstr);
    if (!a || !b) { return 0; }
    return Math.round((b.getTime() - a.getTime()) / DAY_MS);
  }

  function prettyDate(iso) {
    var d = toDate(iso);
    if (!d) { return ''; }
    return DAY_NAMES[d.getDay()] + ' ' + d.getDate() + ' ' + MONTH_NAMES[d.getMonth()];
  }

  /* 'Today', '3 days late', 'in 5 days', 'Sat 4 Oct' ... */
  function dueLabel(iso) {
    if (!iso) { return 'Finished'; }
    var diff = dayDiff(today(), iso);
    if (diff === 0) { return 'Due today'; }
    if (diff === 1) { return 'Tomorrow'; }
    if (diff === -1) { return '1 day late'; }
    if (diff < 0) { return (-diff) + ' days late'; }
    if (diff <= 6) { return 'In ' + diff + ' days'; }
    return prettyDate(iso);
  }

  function cacheSave() {
    try { window.localStorage.setItem(CACHE_KEY, JSON.stringify(state)); }
    catch (err) { /* private browsing, or the disk is full - not fatal */ }
  }

  function cacheLoad() {
    try {
      var raw = window.localStorage.getItem(CACHE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (err) { return null; }
  }

  var toastTimer = null;
  function toast(message) {
    var box = byId('toast');
    box.getElementsByTagName('span')[0].innerHTML = esc(message);
    box.className = 'is-up';
    if (toastTimer) { window.clearTimeout(toastTimer); }
    toastTimer = window.setTimeout(function () { box.className = ''; }, 2600);
  }

  function setOffline(isOffline) {
    byId('offline').className = isOffline ? 'is-up' : '';
  }

  // ------------------------------------------------------------------ api

  function request(method, path, payload, onOk, onFail) {
    var xhr = new XMLHttpRequest();
    inFlight++;
    xhr.open(method, path, true);
    if (payload) { xhr.setRequestHeader('Content-Type', 'application/json'); }
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      inFlight--;
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (err) { data = null; }
      if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
        setOffline(false);
        if (onOk) { onOk(data); }
      } else {
        var message = (data && data.error) ? data.error : 'Could not reach the server';
        if (!xhr.status) { setOffline(true); }
        if (onFail) { onFail(message); } else { toast(message); }
      }
    };
    xhr.send(payload ? JSON.stringify(payload) : null);
  }

  function loadState(onDone) {
    // The cache buster matters: iOS 9 will happily serve a stale GET.
    request('GET', '/api/state?_=' + new Date().getTime(), null, function (data) {
      adopt(data.state);
      if (onDone) { onDone(true); }
    }, function (message) {
      var cached = cacheLoad();
      if (cached && !state) {
        state = cached;
        setOffline(true);
        render();
      }
      toast(message);
      if (onDone) { onDone(false); }
    });
  }

  function adopt(next) {
    state = next;
    cacheSave();
    render();
  }

  // --------------------------------------------------------------- lookups

  function taskById(id) {
    if (!state) { return null; }
    for (var i = 0; i < state.tasks.length; i++) {
      if (state.tasks[i].id === id) { return state.tasks[i]; }
    }
    return null;
  }

  function visibleTasks() {
    var out = [];
    if (!state) { return out; }
    for (var i = 0; i < state.tasks.length; i++) {
      var task = state.tasks[i];
      if (task.archived) { continue; }
      if (areaFilter !== 'all' && (task.area || 'Misc') !== areaFilter) { continue; }
      out.push(task);
    }
    return out;
  }

  function byDueDate(a, b) {
    var da = a.next_due || '9999-12-31';
    var db = b.next_due || '9999-12-31';
    if (da !== db) { return da < db ? -1 : 1; }
    return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  }

  function doneToday(task) {
    return task.last_completed === today();
  }

  // ---------------------------------------------------------------- render

  function render() {
    if (!state) { return; }
    renderHud();
    renderTabs();
    if (view === 'today') { renderToday(); }
    else if (view === 'tasks') { renderTasks(); }
    else { renderHistory(); }
  }

  function renderHud() {
    var left = 0;
    var all = state.tasks;
    for (var i = 0; i < all.length; i++) {
      var due = all[i].next_due;
      if (!all[i].archived && due && dayDiff(today(), due) <= 0) { left++; }
    }
    byId('hud-left').innerHTML = left === 0
      ? 'BASE IS CLEAN'
      : left + (left === 1 ? ' TASK LEFT' : ' TASKS LEFT');
  }

  function renderTabs() {
    var tabs = document.getElementsByClassName('tab');
    for (var i = 0; i < tabs.length; i++) {
      var isOn = tabs[i].getAttribute('data-view') === view;
      tabs[i].className = isOn ? 'tab is-active' : 'tab';
    }
    var views = ['today', 'tasks', 'history'];
    for (var j = 0; j < views.length; j++) {
      byId('view-' + views[j]).className = views[j] === view ? 'view is-active' : 'view';
    }
  }

  function renderFilters() {
    var areas = state.areas || [];
    var html = '<button class="filter' + (areaFilter === 'all' ? ' is-active' : '') +
               '" type="button" data-action="filter" data-area="all">All rooms</button>';
    for (var i = 0; i < areas.length; i++) {
      var on = areaFilter === areas[i] ? ' is-active' : '';
      html += '<button class="filter' + on + '" type="button" data-action="filter" data-area="' +
              esc(areas[i]) + '">' + esc(areas[i]) + '</button>';
    }
    byId('filters').innerHTML = html;
  }

  function taskChips(task, late) {
    var html = '<span class="chip chip-area">' + esc(task.area || 'Misc') + '</span>';
    html += '<span class="chip ' + (late ? 'chip-late' : 'chip-due') + '">' +
            esc(dueLabel(task.next_due)) + '</span>';
    if (task.assignee) {
      html += '<span class="chip chip-who">' + esc(task.assignee) + '</span>';
    }
    if (task.streak > 1) {
      html += '<span class="chip chip-streak">' + task.streak + ' in a row</span>';
    }
    return html;
  }

  function taskRow(task, mode) {
    var isDone = mode === 'done';
    var late = !isDone && task.next_due && dayDiff(today(), task.next_due) < 0;
    var busy = pending[task.id];

    var classes = 'task';
    if (isDone) { classes += ' is-done'; }
    else if (late) { classes += ' is-overdue'; }

    var mark = busy ? '&hellip;' : (isDone ? '&#10003;' : '&nbsp;');
    var boxAction = busy ? 'busy' : (isDone ? 'undo' : 'complete');

    var html = '<div class="' + classes + '">';
    html += '<button class="checkbox' + (isDone ? ' is-checked' : '') + '" type="button" data-action="' +
            boxAction + '" data-id="' + esc(task.id) + '">' + mark + '</button>';
    html += '<div class="task-body">';
    html += '<div class="task-name">' + esc(task.name) + '</div>';
    html += '<div class="task-meta">' + taskChips(task, late) + '</div>';
    if (mode === 'all') {
      html += '<div class="task-meta">' + esc(task.schedule_text || '') +
              (task.completions ? ' &middot; done ' + task.completions + 'x' : '') + '</div>';
    }
    if (task.notes) {
      html += '<div class="task-notes">' + esc(task.notes) + '</div>';
    }
    html += '</div>';

    html += '<div class="task-actions">';
    if (mode === 'all') {
      html += '<button class="btn btn-small btn-brown" type="button" data-action="edit" data-id="' +
              esc(task.id) + '">Edit</button>';
    } else if (!isDone && !busy) {
      html += '<button class="btn btn-small" type="button" data-action="snooze" data-id="' +
              esc(task.id) + '">+1 day</button>';
    }
    html += '</div></div>';
    return html;
  }

  function section(title, tasks, mode, extraClass) {
    if (!tasks.length) { return ''; }
    var html = '<div class="section ' + (extraClass || '') + '">';
    html += '<div class="section-title">' + esc(title) +
            '<span class="count">' + tasks.length + '</span></div>';
    for (var i = 0; i < tasks.length; i++) {
      html += taskRow(tasks[i], mode);
    }
    return html + '</div>';
  }

  function renderToday() {
    renderFilters();

    var tasks = visibleTasks().sort(byDueDate);
    var overdue = [], due = [], soon = [], done = [];

    for (var i = 0; i < tasks.length; i++) {
      var task = tasks[i];
      if (doneToday(task)) { done.push(task); continue; }
      if (!task.next_due) { continue; }
      var diff = dayDiff(today(), task.next_due);
      if (diff < 0) { overdue.push(task); }
      else if (diff === 0) { due.push(task); }
      else if (diff <= 7) { soon.push(task); }
    }

    var html = '';
    html += section('Overdue', overdue, 'today', 'section-overdue');
    html += section('Due today', due, 'today', '');

    if (!overdue.length && !due.length) {
      html += '<div class="empty">' +
              (done.length ? 'Every chore is done. The base is spotless!'
                           : 'Nothing due today. Go build something.') +
              '</div>';
    }

    html += section('Coming up this week', soon, 'today', 'section-later');
    html += section('Checked off today', done, 'done', '');
    byId('today-body').innerHTML = html;
  }

  function renderTasks() {
    var tasks = visibleTasks().slice().sort(function (a, b) {
      var aa = (a.area || 'Misc').toLowerCase();
      var bb = (b.area || 'Misc').toLowerCase();
      if (aa !== bb) { return aa < bb ? -1 : 1; }
      return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
    });

    if (!tasks.length) {
      byId('tasks-body').innerHTML = '<div class="empty">No tasks yet. Craft one above.</div>';
      return;
    }

    var html = '';
    var area = null;
    for (var i = 0; i < tasks.length; i++) {
      var current = tasks[i].area || 'Misc';
      if (current !== area) {
        if (area !== null) { html += '</div>'; }
        html += '<div class="section"><div class="section-title">' + esc(current) + '</div>';
        area = current;
      }
      html += taskRow(tasks[i], 'all');
    }
    byId('tasks-body').innerHTML = html + '</div>';
  }

  function renderHistory() {
    var log = state.log || [];
    var todayISO = today();

    var todayCount = 0, doneWeek = 0, best = 0, active = 0;
    for (var i = 0; i < log.length; i++) {
      var diff = dayDiff(log[i].date, todayISO);
      if (diff === 0) { todayCount++; }
      if (diff >= 0 && diff < 7) { doneWeek++; }
    }
    for (var j = 0; j < state.tasks.length; j++) {
      if (state.tasks[j].archived) { continue; }
      active++;
      if ((state.tasks[j].streak || 0) > best) { best = state.tasks[j].streak; }
    }

    byId('stats').innerHTML =
      '<div class="stat"><b>' + todayCount + '</b><span>Done today</span></div>' +
      '<div class="stat"><b>' + doneWeek + '</b><span>Done this week</span></div>' +
      '<div class="stat"><b>' + best + '</b><span>Best streak</span></div>' +
      '<div class="stat"><b>' + active + '</b><span>Active tasks</span></div>';

    if (!log.length) {
      byId('history-body').innerHTML =
        '<div class="empty">Nothing in the logbook yet.</div>';
      return;
    }

    var html = '';
    var day = null;
    for (var k = 0; k < log.length; k++) {
      var entry = log[k];
      if (entry.date !== day) {
        day = entry.date;
        var heading = entry.date === todayISO ? 'Today' : prettyDate(entry.date);
        html += '<div class="log-day">' + esc(heading) + '</div>';
      }
      html += '<div class="log-entry"><span>' + esc(entry.name) +
              ' <span class="chip chip-area">' + esc(entry.area || 'Misc') + '</span></span></div>';
    }
    byId('history-body').innerHTML = html;
  }

  // ---------------------------------------------------------------- writes

  function write(task, path, payload, successMessage) {
    if (pending[task.id]) { return; }
    pending[task.id] = true;
    render();
    request('POST', '/api/tasks/' + encodeURIComponent(task.id) + path, payload || {},
      function (data) {
        delete pending[task.id];
        adopt(data.state);
        if (successMessage) { toast(successMessage); }
      },
      function (message) {
        delete pending[task.id];
        render();
        toast(message);
      });
  }

  function completeTask(id) {
    var task = taskById(id);
    if (!task) { return; }
    write(task, '/complete', { date: today() }, 'Done: ' + task.name);
  }

  function undoTask(id) {
    var task = taskById(id);
    if (!task) { return; }
    write(task, '/undo', {}, 'Put back: ' + task.name);
  }

  function snoozeTask(id) {
    var task = taskById(id);
    if (!task) { return; }
    write(task, '/snooze', { days: 1 }, 'Pushed to tomorrow');
  }

  // ---------------------------------------------------------------- editor

  function showEditorFields() {
    var type = byId('f-type').value;
    byId('wrap-days').style.display = type === 'days' ? 'block' : 'none';
    byId('wrap-weekly').style.display = type === 'weekly' ? 'block' : 'none';
    byId('wrap-monthly').style.display = type === 'monthly' ? 'block' : 'none';
    byId('wrap-once').style.display = type === 'once' ? 'block' : 'none';
    byId('wrap-start').style.display = type === 'once' ? 'none' : 'block';
  }

  function paintWeekdays() {
    var buttons = byId('f-weekdays').getElementsByTagName('button');
    for (var i = 0; i < buttons.length; i++) {
      var day = Number(buttons[i].getAttribute('data-day'));
      var on = false;
      for (var j = 0; j < weekdays.length; j++) {
        if (weekdays[j] === day) { on = true; }
      }
      buttons[i].className = on ? 'day is-on' : 'day';
    }
  }

  function openEditor(id) {
    var task = id ? taskById(id) : null;
    editing = task ? task.id : null;

    byId('editor-title').innerHTML = task ? 'Edit task' : 'New task';
    byId('editor-delete-row').style.display = task ? '' : 'none';

    byId('f-name').value = task ? task.name : '';
    byId('f-area').value = task ? (task.area || '') : '';
    byId('f-assignee').value = task ? (task.assignee || '') : '';
    byId('f-notes').value = task ? (task.notes || '') : '';
    byId('f-start').value = (task && task.start) ? task.start : today();

    var rec = (task && task.recurrence) ? task.recurrence : { type: 'days', every: 7 };
    byId('f-type').value = rec.type || 'days';
    byId('f-every').value = rec.every || 7;
    byId('f-dom').value = rec.day || 1;
    byId('f-date').value = rec.date || today();
    weekdays = (rec.type === 'weekly' && rec.days && rec.days.length) ? rec.days.slice() : [1];

    paintWeekdays();
    showEditorFields();
    byId('editor').className = 'modal is-open';
  }

  function closeEditor() {
    byId('editor').className = 'modal';
    editing = null;
  }

  function collectRecurrence() {
    var type = byId('f-type').value;
    if (type === 'weekly') {
      return { type: 'weekly', days: weekdays.length ? weekdays : [1] };
    }
    if (type === 'monthly') {
      return { type: 'monthly', day: Number(byId('f-dom').value) || 1 };
    }
    if (type === 'once') {
      return { type: 'once', date: byId('f-date').value || today() };
    }
    return { type: 'days', every: Number(byId('f-every').value) || 1 };
  }

  function saveEditor() {
    var name = byId('f-name').value.replace(/^\s+|\s+$/g, '');
    if (!name) { toast('Give the task a name first'); return; }
    if (inFlight > 0) { return; }

    var payload = {
      name: name,
      area: byId('f-area').value.replace(/^\s+|\s+$/g, '') || 'Misc',
      assignee: byId('f-assignee').value.replace(/^\s+|\s+$/g, ''),
      notes: byId('f-notes').value.replace(/^\s+|\s+$/g, ''),
      recurrence: collectRecurrence(),
      start: byId('f-start').value || today()
    };

    var path = editing ? '/api/tasks/' + encodeURIComponent(editing) : '/api/tasks';
    var wasEditing = editing;
    request('POST', path, payload, function (data) {
      closeEditor();
      adopt(data.state);
      toast(wasEditing ? 'Task updated' : 'Task crafted');
    });
  }

  function deleteEditing() {
    if (!editing) { return; }
    var task = taskById(editing);
    var label = task ? task.name : 'this task';
    if (!window.confirm('Delete "' + label + '" for good?')) { return; }
    request('POST', '/api/tasks/' + encodeURIComponent(editing) + '/delete', {}, function (data) {
      closeEditor();
      adopt(data.state);
      toast('Task deleted');
    });
  }

  // ---------------------------------------------------------------- events

  function findAction(node) {
    while (node && node !== document.body) {
      if (node.getAttribute && node.getAttribute('data-action')) { return node; }
      node = node.parentNode;
    }
    return null;
  }

  function dispatch(node) {
    var action = node.getAttribute('data-action');
    var id = node.getAttribute('data-id');

    if (action === 'tab') {
      view = node.getAttribute('data-view');
      render();
      window.scrollTo(0, 0);
    } else if (action === 'filter') {
      areaFilter = node.getAttribute('data-area');
      render();
    } else if (action === 'complete') {
      completeTask(id);
    } else if (action === 'undo') {
      undoTask(id);
    } else if (action === 'snooze') {
      snoozeTask(id);
    } else if (action === 'edit') {
      openEditor(id);
    } else if (action === 'new') {
      openEditor(null);
    } else if (action === 'save') {
      saveEditor();
    } else if (action === 'close') {
      closeEditor();
    } else if (action === 'delete') {
      deleteEditing();
    } else if (action === 'weekday') {
      var day = Number(node.getAttribute('data-day'));
      var next = [];
      var found = false;
      for (var i = 0; i < weekdays.length; i++) {
        if (weekdays[i] === day) { found = true; } else { next.push(weekdays[i]); }
      }
      if (!found) { next.push(day); }
      weekdays = next.sort(function (a, b) { return a - b; });
      paintWeekdays();
    }
  }

  function isFormField(node) {
    var tag = node && node.tagName ? node.tagName.toLowerCase() : '';
    return tag === 'input' || tag === 'select' || tag === 'textarea';
  }

  /* Safari 9 waits ~300ms before firing click.  Acting on touchend makes
     every tap feel immediate; the timestamp stops the click firing twice. */
  var touchX = 0, touchY = 0, touchMoved = false;

  function onTouchStart(event) {
    if (event.touches.length !== 1) { touchMoved = true; return; }
    touchX = event.touches[0].clientX;
    touchY = event.touches[0].clientY;
    touchMoved = false;
  }

  function onTouchMove(event) {
    if (!event.touches.length) { return; }
    if (Math.abs(event.touches[0].clientX - touchX) > 10 ||
        Math.abs(event.touches[0].clientY - touchY) > 10) {
      touchMoved = true;
    }
  }

  function onTouchEnd(event) {
    if (touchMoved || isFormField(event.target)) { return; }
    var node = findAction(event.target);
    if (!node) { return; }
    event.preventDefault();
    lastTap = new Date().getTime();
    dispatch(node);
  }

  function onClick(event) {
    if (new Date().getTime() - lastTap < 700) { return; }
    var node = findAction(event.target);
    if (!node) { return; }
    event.preventDefault();
    dispatch(node);
  }

  // ------------------------------------------------------------------ boot

  function boot() {
    document.addEventListener('click', onClick, false);
    document.addEventListener('touchstart', onTouchStart, false);
    document.addEventListener('touchmove', onTouchMove, false);
    document.addEventListener('touchend', onTouchEnd, false);
    byId('f-type').onchange = showEditorFields;

    // Tapping the dark surround closes the editor.
    byId('editor').addEventListener('click', function (event) {
      if (event.target === byId('editor')) { closeEditor(); }
    }, false);

    loadState();

    // Other iPads in the house may be checking things off too.
    window.setInterval(function () {
      if (inFlight > 0) { return; }
      if (byId('editor').className.indexOf('is-open') !== -1) { return; }
      loadState();
    }, REFRESH_MS);

    // Coming back from the home screen should show fresh numbers.
    window.addEventListener('pageshow', function () {
      if (state) { loadState(); }
    }, false);
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    boot();
  } else {
    document.addEventListener('DOMContentLoaded', boot, false);
  }
}());
