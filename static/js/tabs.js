import { store } from './state.js';

export function switchTab(name) {
  store.currentTab = name;
  // Match each tab by extracting its name from the onclick attribute — avoids
  // any reliance on DOM order which would break after tab reordering.
  document.querySelectorAll('.tab').forEach(t => {
    const m = (t.getAttribute('onclick') || '').match(/switchTab\('(\w+)'\)/);
    t.classList.toggle('active', !!(m && m[1] === name));
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'overview' && !store.overviewState.loaded) window.loadOverview();
  if (name === 'history' && !store.historyState.loaded) window.loadHistory();
  if (name === 'gains' && !store.gainsState.loaded) window.loadGains();
  if (name === 'orders' && !store.ordersState.loaded) window.loadOrders();
  if (name === 'quotes') window.initWatchlists();
  if (name === 'incomepnl' && !store.incomePnlState.loaded) {
    window.loadIncomeStats();
    window.loadIncomeTrades();
  }
  if (name === 'strategy' && store._stratTicker) {
    window.loadStrategySuggestions(store._stratTicker);
    window.loadStrategyOrders();
  }
}

export function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (store.currentTab === 'overview') window.loadOverview();
  if (store.currentTab === 'positions') window.loadPositions();
  if (store.currentTab === 'quotes') window.loadQuotes();
  if (store.currentTab === 'history') window.loadHistory();
  if (store.currentTab === 'gains') window.loadGains();
  if (store.currentTab === 'orders') window.loadOrders();
  if (store.currentTab === 'incomepnl') {
    window.loadIncomeStats();
    window.loadIncomeTrades();
  }
  if (store.currentTab === 'strategy' && store._stratTicker) {
    window.loadStrategySuggestions(store._stratTicker);
    window.loadStrategyOrders();
    window.loadStrategyRecent();
  }
}
