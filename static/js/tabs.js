import { store } from './state.js';

export function switchTab(name) {
  store.currentTab = name;
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'overview' && !store.overviewState.loaded) window.loadOverview();
  if (name === 'balance' && !store.balanceState.loaded) window.loadBalance();
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
  if (name === 'analytics' && !store.analyticsState.loaded) window.loadAnalytics();
  if (name === 'consolidation' && !store.consolidationState.loaded) window.loadConsolidation();
}

export function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (store.currentTab === 'overview') window.loadOverview();
  if (store.currentTab === 'balance') window.loadBalance();
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
  if (store.currentTab === 'analytics') window.loadAnalytics();
  if (store.currentTab === 'consolidation') window.loadConsolidation();
}
