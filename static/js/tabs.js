import * as state from './state.js';
import {
  overviewState,
  historyState,
  gainsState,
  ordersState,
  incomePnlState,
  _stratTicker,
} from './state.js';

export function switchTab(name) {
  state.currentTab = name;
  // Match each tab by extracting its name from the onclick attribute — avoids
  // any reliance on DOM order which would break after tab reordering.
  document.querySelectorAll('.tab').forEach(t => {
    const m = (t.getAttribute('onclick') || '').match(/switchTab\('(\w+)'\)/);
    t.classList.toggle('active', !!(m && m[1] === name));
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'overview'  && !overviewState.loaded)  window.loadOverview();
  if (name === 'history'   && !historyState.loaded)   window.loadHistory();
  if (name === 'gains'     && !gainsState.loaded)     window.loadGains();
  if (name === 'orders'    && !ordersState.loaded)    window.loadOrders();
  if (name === 'quotes')   window.initWatchlists();
  if (name === 'incomepnl' && !incomePnlState.loaded) { window.loadIncomeStats(); window.loadIncomeTrades(); }
  if (name === 'strategy' && _stratTicker) {
    window.loadStrategySuggestions(_stratTicker);
    window.loadStrategyOrders();
  }
}

export function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (state.currentTab === 'overview')  window.loadOverview();
  if (state.currentTab === 'positions') window.loadPositions();
  if (state.currentTab === 'quotes')    window.loadQuotes();
  if (state.currentTab === 'history')   window.loadHistory();
  if (state.currentTab === 'gains')     window.loadGains();
  if (state.currentTab === 'orders')    window.loadOrders();
  if (state.currentTab === 'incomepnl') { window.loadIncomeStats(); window.loadIncomeTrades(); }
  if (state.currentTab === 'strategy' && _stratTicker) {
    window.loadStrategySuggestions(_stratTicker);
    window.loadStrategyOrders();
    window.loadStrategyRecent();
  }
}
