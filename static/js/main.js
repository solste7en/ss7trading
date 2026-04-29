import { debounce } from './utils.js';
import { store } from './state.js';

import { switchTab, refreshCurrent } from './tabs.js';
import {
  loadOverview, loadTickerPage, openLadder,
  searchCustomTicker, loadCustomTickerPage,
} from './overview.js';
import { loadBalance } from './balance.js';
import {
  loadPositions, sortPositions, bindPositionsDnD, togglePosGroup,
  movePositionList, showNewPositionListForm, hideNewPositionListForm,
  createPositionList, beginPositionListRename, deletePositionList,
  _renderPositionCharts,
} from './positions.js';
import {
  initWatchlists, selectWatchlist, showNewListForm, hideNewListForm,
  createWatchlist, deleteCurrentList, addWatchlistSymbol,
  removeWatchlistSymbol, loadQuotes, sortQuotes, fetchQuote,
} from './quotes.js';
import {
  loadHistory, closeSyncModal, syncTrades, loadGains, renderPagination,
  setHistorySort, setGainsSort,
} from './history.js';
import {
  loadIncomeStats, loadIncomeTrades, setIncomePnlSort, setIpCardFilter,
  toggleIncomeTrade, dismissRecovery, syncIncome, toggleSyncDropdown, _ipOnStatusChange,
  onIpDateRangeChange, onIpCustomDateChange,
  toggleRecoverySummary,
} from './income.js';
import {
  openTrade, setTradeMode, onTradeTickerChange, updateEqFields, updateOptFields,
  previewOrder, submitPendingOrder, clearTradeResult, showTradeError,
  loadTradeQuote, loadTradingViewChart, loadOptionExpirations,
  loadOptionChain, onChainClick, loadPositionSummaryForTicker,
  clearPositionPanel, syncTradeTicker,
  onTradeExpiryChange, onTradeOptContextChange, onTradeStrikeChange,
  loadTradeOrders, cancelTradeOrder,
} from './trade.js';
import {
  renderRungs, addRung, removeRung, updateLadderSummary,
  ladderEvenSplit, ladderScaleUp, ladderScaleDown,
  onLadderTickerChange, loadLadderSuggest, applyLadderSuggest,
  loadLadderRecent, loadLiveVerify, loadLadderOrders,
  cancelLadderOrder, cancelAllLadderOrders, previewLadder, submitLadder,
} from './ladder.js';
import {
  loadOrders, sortOrders, filterOrders, cancelOrder,
} from './orders.js';
import {
  loadAnalytics, loadConsolidation, loadConsolidationDetail,
  selectConsolidationList,
} from './analytics.js';
import {
  setStrategyMode, onStrategyTickerChange, loadStrategyExpirations,
  loadStrategyChain, shiftChainPage, onStratChainClick,
  loadStrategySuggestions, applyStrategySuggestion,
  loadStrategyRecent, loadStrategyOrders,
  cancelStratOrder, cancelAllStratOrders,
  updateStratPnl, previewStrategy, submitStrategy,
  _onStratLadderToggle, _renderStratLadderRungs,
  _populateStrikeDropdowns, _autoCalcStratPrice,
} from './strategy.js';

// Expose all functions that HTML inline handlers need on window
Object.assign(window, {
  // tabs
  switchTab, refreshCurrent,
  // analytics
  loadAnalytics, loadConsolidation, loadConsolidationDetail, selectConsolidationList,
  // overview
  loadOverview, loadTickerPage, openLadder, searchCustomTicker, loadCustomTickerPage,
  loadBalance,
  // positions
  loadPositions, sortPositions, bindPositionsDnD, togglePosGroup,
  movePositionList, showNewPositionListForm, hideNewPositionListForm,
  createPositionList, beginPositionListRename, deletePositionList,
  // quotes
  initWatchlists, selectWatchlist, showNewListForm, hideNewListForm,
  createWatchlist, deleteCurrentList, addWatchlistSymbol,
  removeWatchlistSymbol, loadQuotes, sortQuotes, fetchQuote,
  // history
  loadHistory, closeSyncModal, syncTrades, loadGains, renderPagination,
  setHistorySort, setGainsSort,
  // income
  loadIncomeStats, loadIncomeTrades, setIncomePnlSort, setIpCardFilter,
  toggleIncomeTrade, dismissRecovery, syncIncome, toggleSyncDropdown, _ipOnStatusChange,
  onIpDateRangeChange, onIpCustomDateChange, toggleRecoverySummary,
  // trade
  openTrade, setTradeMode, onTradeTickerChange, updateEqFields, updateOptFields,
  previewOrder, submitPendingOrder, clearTradeResult, showTradeError,
  loadTradeQuote, loadTradingViewChart, loadOptionExpirations,
  loadOptionChain, onChainClick, loadPositionSummaryForTicker,
  clearPositionPanel, syncTradeTicker,
  onTradeExpiryChange, onTradeOptContextChange, onTradeStrikeChange,
  loadTradeOrders, cancelTradeOrder,
  // ladder
  renderRungs, addRung, removeRung, updateLadderSummary,
  ladderEvenSplit, ladderScaleUp, ladderScaleDown,
  onLadderTickerChange, loadLadderSuggest, applyLadderSuggest,
  loadLadderRecent, loadLiveVerify, loadLadderOrders,
  cancelLadderOrder, cancelAllLadderOrders, previewLadder, submitLadder,
  // orders
  loadOrders, sortOrders, filterOrders, cancelOrder,
  // strategy
  setStrategyMode, onStrategyTickerChange, loadStrategyExpirations,
  loadStrategyChain, shiftChainPage, onStratChainClick,
  loadStrategySuggestions, applyStrategySuggestion,
  loadStrategyRecent, loadStrategyOrders,
  cancelStratOrder, cancelAllStratOrders,
  updateStratPnl, previewStrategy, submitStrategy,
  _onStratLadderToggle, _renderStratLadderRungs,
  _populateStrikeDropdowns, _autoCalcStratPrice,
  // utils needed by inline handlers
  debounce,
});

// Expose shared state that inline handlers reference
window._paginationRegistry = store._paginationRegistry;
window.ladderRungs = store.ladderRungs;
window.customTickerState = store.customTickerState;

// Theme picker
(function initThemePicker() {
  const STORAGE_KEY = 'ss7-theme';
  const VALID = new Set(['midnight','ember','ocean','forest','rose','daylight','sand','cloud']);
  const picker = document.getElementById('theme-picker');
  if (!picker) return;
  const swatches = picker.querySelectorAll('.theme-swatch');
  let current = localStorage.getItem(STORAGE_KEY) || 'midnight';
  if (!VALID.has(current)) current = 'midnight';

  function apply(name, rerender = false) {
    if (name === 'midnight') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', name);
    }
    localStorage.setItem(STORAGE_KEY, name);
    swatches.forEach(s => s.classList.toggle('active', s.dataset.theme === name));
    // Re-render position charts so legend/border colors update immediately
    if (rerender) _renderPositionCharts();
  }

  swatches.forEach(s => s.addEventListener('click', () => apply(s.dataset.theme, true)));
  apply(current);
})();

// Bootstrap
document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
loadPositions();
loadOverview();
initWatchlists();
renderRungs();
